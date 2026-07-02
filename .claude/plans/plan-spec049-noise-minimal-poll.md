# Plan: spec 049 noise skip cycle の 0xE7 単独 minimal poll 降格

## Goal

noisy cycle を完全 skip → 0xE7 単独 (OPC=1) minimal poll に降格し、noise 帯の欠損 1.5〜6/h を live に回収する。spec 012 の reconnect storm 防止は timeout counter 分離で維持。

## TDD ステップ

### Step 1: pure helper `decide_noise_cycle_action`

- 引数: (noisy, streak, max_consecutive, minimal_enabled) → "normal" / "skip" / "minimal"
- not noisy → "normal"、noisy && streak >= max → "normal" (fail-safe)、noisy && minimal_enabled → "minimal"、noisy && not enabled → "skip"
- テスト: 新 test_noise_minimal_poll.py

### Step 2: DiagState 3 counter

- noise_minimal_polls_total / noise_minimal_poll_success_total / noise_minimal_poll_timeout_total
- on_noise_minimal_poll() / on_noise_minimal_poll_success() / on_noise_minimal_poll_timeout()
- snapshot (zero-omit しない) + DIAG_SENSOR_DEFS 3 entry + test_diag_state 期待 dict 更新

### Step 3: apply_defaults + NOISE_MINIMAL_EPCS

- `noise_skip_tier1_minimal_poll_enabled` default True
- `NOISE_MINIMAL_EPCS = [0xE7]` 定数

### Step 4: main loop 配線 (テストは helper 経由、配線は目視 + 既存 suite green)

- noisy 分岐を decide_noise_cycle_action の 3 値分岐に書き換え:
  - "skip" → 既存 (sleep + continue)
  - "minimal" → `_noise_minimal = True` を立てて fall through、on_noise_adaptive_skip + streak++ + on_noise_minimal_poll は従来位置で
  - "normal" → streak リセット (既存)
- epc 選択: `_noise_minimal` なら cycle_epcs = NOISE_MINIMAL_EPCS、tier 判定と normal_cycle_count 増加を skip、last_normal_poll_start も進めない (既存 skip と同等)
- timeout 分岐: `_noise_minimal` なら on_noise_minimal_poll_timeout のみ、on_erxudp_timeout / on_poll_failure / force reconnect 判定を skip
- 成功分岐: `_noise_minimal` でも通常 publish (kind == "normal" 経路)、追加で on_noise_minimal_poll_success

### Step 5: compose telegraf 3 topic (deploy 時)

## Risk

- main loop の `_noise_minimal` flag が probe / burst / catchup と重ならないこと (noisy 分岐は kind == "normal" 限定なので構造上安全)
- EEDSCAN は 300s 間隔なので noisy 判定は最大 5 分 stale — 既存挙動と同じ、変更しない
