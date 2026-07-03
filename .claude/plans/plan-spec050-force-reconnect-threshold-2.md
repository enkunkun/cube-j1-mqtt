# Plan: spec 050 erxudp_timeout_force_reconnect_threshold default 6 → 2

設定 default 変更のみ (= TDD は既存テスト更新で最小)。

1. tests/unit/test_apply_defaults_spec_032.py の `test_default_erxudp_timeout_force_reconnect_threshold_is_6` を spec 050 の期待値 2 に更新 (Red) — 明示 override 30 のテストは不変
2. mqtt_bridge.py `apply_defaults` の setdefault 6 → 2 (Green)
3. commit → fork push → lab-ub01 deploy
4. **deploy 後に実機 config.json から override key 削除 + bridge 再起動** → /api/config で threshold=2 (default 由来) verify
5. todo.md の override 警告を [x] に
