# spec 048 観測記録: 正式 SC 判定 (deploy +6h 窓 + overnight 10h 裏取り)

**Deploy**: 2026-07-02 19:41 JST (= 10:41 UTC、uptime 逆算実時刻)、version `1.0.0+4e51b77`
**判定時刻**: 2026-07-03 06:04 JST。6h 窓は過去範囲 query (= eval @ 2026-07-02T16:41Z) で deploy 後に正確に一致させた。

## 結果

### 6h 窓 (= 19:41〜01:41 JST)

| SC | 基準 | 実測 | 判定 |
|---|---|---|---|
| SC-1 | esv_inf ≈ 0 / inf_ignored ≈ 36 | esv_inf = **0** / inf_ignored = 45 (= 7.5/h) | ✅ |
| SC-2 | live ≥ 90 件 (= 15/h) | **157 件 = 26.2/h** (= baseline 12.5/h の 2.1 倍) | ✅ 目標帯 19〜22/h も超過 |
| SC-3 | backfill 継続 + lag 60-300s 減衰 | recovered = 0 / lag 60-300s = **0** | ✅ chain 完全消滅 (下記) |
| 監視 | timeouts +4/h 上振れ想定 | 29.4/h (= baseline 横ばい、上振れ無し) | ✅ |
| 監視 | wisun_reconnects 大幅増なら rollback | 6.5/h (= baseline 6.7/h 不変) | ✅ |

### overnight 10h 裏取り (= 20:03〜06:03 JST)

live 255 件 (= 25.5/h) 持続、inf_ignored 7.6/h、recovered 0、timeouts 30.4/h 横ばい、reconnects 6.6/h 不変。

## 解釈

- **live 取得率 21% → 43% (2 倍)**。INF filter (= FR-001) 単独で chain (= H3) まで消滅した: `recovered_from_mismatch = 0` が 10h 継続 = そもそも late frame が発生しない。**従来の「遅延応答 chain」は INF の cycle 乗っ取りが人工的に作っていた**もので、メーターは邪魔さえなければ p50 3.3s で普通に応答する
- SC-3 の「backfill 継続」は空振りではなく **backfill 機構が不要になった** (= rescue する対象が消えた)。FR-003 の stash-and-continue 経路は reconnect 直後等の稀な late frame への保険として残る
- 収支 (overnight): 60 − 25.5 (live) − 30.4 (timeout) ≈ 4/h (= noise skip 帯 + reconnect downtime) で閉じる
- 残る欠損は timeouts ≈ 30/h = 純粋な RX 損失 (= PANA reauth 周期、memory `feedback-erxudp-timeouts-periodic-pana` の「メーター仕様で touch 不能」領域)。software 側の次の一手は noise skip 帯 (≈ 6/h) の tier1 免除 (= tako 合議 2026-07-02 案 3)、根治は spec 031 CT クランプ (hardware)

## 判定

**spec 048 = SC 全達成、close。**
