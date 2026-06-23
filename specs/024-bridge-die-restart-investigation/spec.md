# Investigation Spec: Bridge "die-restart loop" の原因究明

**Feature Branch**: `024-bridge-die-restart-investigation`
**Created**: 2026-06-23
**Status**: Investigation Complete (fix is spec 025)
**Input**: spec 023 deploy 検証中に「bridge が die-restart loop に入っている」と私が誤判定し 30 分浪費。 ユーザコメント「spec 024 着手して原因究明」。

## Background

spec 023 (commit `5b37fbf9`) deploy 直後の bridge 動作確認で 14 分間に 8 回 SKRESET 観測、 「bridge が die-restart loop」 と私が判定。 spec 022 ロールバックでも 5 分間に SKRESET 3 回 → 「spec 023 と無関係」と判定し spec 024 (= 原因究明) に pivot。

## Investigation Result (= 結論)

「die-restart loop」 は **誤認**。 実態は **spec 011 force-reconnect mechanism が連続発火していた**。

### 根拠

- dmesg の SIGKILL は全部私の `setprop ctl.stop` 由来 (= 自発的 die 記録なし)
- bridge プロセス健全: pgrep で生存確認、 メモリ 6.9MB RSS / VmSize 38MB / OOM 余地なし (MemFree 888MB / 1GB)
- SKRESET は bridge 起動時だけでなく **同 process 内の `wisun_connect` 再呼び出し時にも実行**。 直前 log で「Opening serial」 / 「HA discovery」 等の起動 sequence が無いことで判別可能
- SKRESET 直前の log で明示的に root cause が記録:
  ```
  10:41:00 warn poll_failure (reason=erxudp_timeout)
  10:41:00 info Main loop error (attempt 1): wisun reconnect forced:
           consecutive_erxudp_timeouts=5, pending=False - reconnecting Wi-SUN in 30s
  10:41:30 info SKRESET
  ```

### メカニズム

spec 011 (および spec 022 force_reconnect helper) で:
- config `erxudp_timeout_force_reconnect_threshold` (default 5)
- `consecutive_erxudp_timeouts` が threshold 到達で `force_wisun_reconnect` フラグ set
- main loop except 経路で `wisun_connect` 再呼び出し (= SKRESET → SKJOIN)
- spec 017 backoff (30/60/120/240/300s) で待ってから retry

つまり「**メーターが ERXUDP に 5 連続で応答しない → wisun セッション切れたと判定 → 強制 reconnect**」 のロジックが頻繁に発火している。

### spec 023 との関係

spec 023 で burst 中 `erxudp_timeout_sec=5` を採用。 メーター応答が悪い時、 5 連続 fail に達する時間が:
- spec 022 (base 30s timeout): 5 × 30 = 150 秒 (= 約 2.5 分)
- spec 023 (burst 5s timeout): 5 × 5 = 25 秒 (= 0.5 分)

spec 023 で reconnect 発火頻度は理論上 **6 倍** に。 実機 SKRESET 頻度の差はそこまで大きくない (spec 022 で 5 分に 3 回 = 100 秒 / 回、 spec 023 で 14 分に 8 回 = 105 秒 / 回) ので、 メーター応答性の悪さが共通要因。

### 共通要因の仮説 (未検証)

- メーター負荷 / 時間帯特性 (= 電力消費高い時間帯で B-route 応答悪化)
- BP35CX 長時間稼働の degradation
- 他機器との Wi-SUN 干渉
- LQI 低下 (今回 LQI=F2 で良好だったので未濃厚)

## Findings — 誤判定の教訓

1. **Android busybox `ps -A` 非対応**: 「bad pid '-A'」 エラーで silent 失敗、 grep で空振り → bridge 死亡と私が誤判定。 30 分浪費。 memory `feedback-android-ps-pgrep.md` に教訓化済
2. **SKRESET = 起動 + reconnect 両方**: SKRESET 単独で「bridge restart」 と解釈しない、 直前 log の起動 sequence 有無で判別

## Hypotheses → 検証結果

| 仮説 | 検証 | 結果 |
|---|---|---|
| bridge process が SIGKILL されている | dmesg | × 自発 die なし |
| Python OOM | meminfo / VmRSS | × メモリ余裕 |
| Python uncaught exception で die | logcat -b crash, stderr | × 出力なし |
| メモリリーク | VmRSS 推移 | × 6.9MB で安定 |
| init service が dead state | init.svc.X getprop | × running、 fork も成功 |
| **spec 011 force_reconnect 連発** | SKRESET 直前 log | ✅ **確定** |

## Out of Scope (= spec 025 で対応)

- fix 案 (= threshold tune / burst 中の threshold 緩和 / backoff 延長 / reconnect 抑制ロジック)
- メーター応答性悪化の根本対策 (= cube-j1 側でできない可能性、 物理層問題なら別)

## Acceptance (= 調査完了条件)

- [x] SKRESET 頻発の root cause を log で確定
- [x] spec 023 と spec 022 の頻度差を計測 (= 仮設 6 倍だが実機差は小)
- [x] 誤判定 (= die-restart) の根拠を否定
- [x] 次 spec (spec 025) の fix 候補リスト化

## 関連

- spec 011 `erxudp_timeout_force_reconnect_threshold` (= 今回の発火源)
- spec 017 rejoin backoff (= reconnect 試行間隔)
- spec 022 RealtimeModeState (= burst mode で 6 倍速発火)
- spec 023 burst_timeout=5s (= fail 加速)
- memory `feedback-android-ps-pgrep.md` (= 誤判定教訓)
