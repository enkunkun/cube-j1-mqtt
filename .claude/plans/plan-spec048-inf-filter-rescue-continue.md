# Plan: spec 048 read_erxudp INF filter + rescue 後継続待ち

## Goal

spec 047 で確定した H2 (INF 混入 59%) / H3 (chain 41%) を read_erxudp の構造変更で解消し、live 取得率 12.5/h → 19〜22/h を狙う。

## TDD ステップ (pure → DiagState → read_erxudp → main loop の型)

### Step 1: DiagState 拡張

- `erxudp_inf_ignored_total` counter (= FR-001/006、zero-omit しない)
- `pending_rescued_frames` = collections.deque(maxlen=8) (= FR-003/NFR-003)
- `on_erxudp_inf_ignored()` method
- `last_recovered_send_ts` bus は撤去 (= FR-007) — on_erxudp_recovered_from_mismatch の send_ts 引数は残す (lag 記録は継続) が bus stash を list stash に変更
- snapshot + DIAG_SENSOR_DEFS 1 entry 追加
- テスト: test_rescued_frame_classify.py or 新 test_spec048_read_continue.py

### Step 2: read_erxudp 構造変更

順序 (= erxudp line 処理内):
1. hex decode + `1081` prefix check (既存)
2. **ESV 抽出 → 0x72/0x52 以外なら on_erxudp_inf_ignored() + continue** (= FR-001、classify_rescued_esv を流用可)
3. expected_tid check:
   - 一致 → return payload (既存)
   - 不一致 → on_erxudp_tid_mismatch (既存) → ring lookup (got_tid のみ、**lookup_latest 撤去** = FR-002)
     - hit → on_erxudp_recovered_from_mismatch + on_erxudp_rescued (spec 047 counter 維持) + **pending_rescued_frames.append((payload, send_ts)) + continue** (= FR-003、return しない)
     - miss → discard (既存 spec 014 path)

テスト (test_read_erxudp_tid.py 追記):
- INF → 後続の expected TID 一致 frame が返る (AC-2)
- INF のみ → None + inf_ignored inc + rescued counter 不変 (AC-1)
- late ring hit → pending に stash + 後続 expected 一致が live で返る (AC-3)
- late ring hit のみ → None (timeout) + pending に stash 済 (AC-4)
- got_tid=0 → discard (lookup_latest 無し、AC-5)
- 既存テスト test_rescue_* 4 件は「即 return」前提 → 新挙動 (stash + None) に更新

### Step 3: main loop drain (= FR-004/005/007)

- 旧 `_late_ts = diag_state.last_recovered_send_ts` 分岐 (mqtt_bridge.py 4840 付近) を撤去
- read_erxudp 呼出し後 (data 有無に関わらず) に drain:
  ```
  while diag_state.pending_rescued_frames:
      payload_r, send_ts_r = diag_state.pending_rescued_frames.popleft()
      props_r = parse_el_response(payload_r)
      m_r = apply_energy_scale(decode_measurements(props_r), coeff, unit_kwh)
      publish_late_frame(mqtt, device_id, m_r, send_ts_r, cfg, diag_state)
  ```
- `publish_late_frame(...)` = 旧 _late_ts 分岐の中身 (late publish + backfill 3 系統 + empty 判定) を関数抽出 (= tidy first: 先に抽出 commit → 挙動変更 commit の順が理想だが、分岐自体が bus 前提なので同 commit で置換)
- data is None の場合は既存 timeout path のまま (= FR-005 は自然に成立: rescue が return しなくなるので)
- テスト: publish_late_frame を FakeMqtt で単体テスト (test_publish_recovery_backfill.py の pattern 流用)

### Step 4: compose telegraf topic 追加 (deploy 時)

- `cubej/+/diag/erxudp_inf_ignored_total` 1 topic

## 挙動変化の明示 (= 観測解釈)

- rescue-only cycle が poll_success → timeout に変わる = timeouts +4/h 程度上振れ、last_poll_success の staleness も正直になる
- esv_inf counter は ~0 になり inf_ignored に移る (= SC-1 の deploy verify シグナル)

## Risk

- read_erxudp は最 hot path — ESV 抽出は 1 byte 参照のみ、pending deque は maxlen bound で安全
- 5 連続 timeout → force reconnect 閾値 (= should_force_wisun_reconnect): rescue-only cycle が timeout 計上になることで reconnect が増える可能性 → consecutive_erxudp_timeouts は「pending に stash があった cycle」ではリセットすべきか？ → **しない** (data 誠実性優先、メーターが自 cycle に応答しない状態は本物の異常)。ただし deploy 後に wisun_reconnects_total の上振れを監視
