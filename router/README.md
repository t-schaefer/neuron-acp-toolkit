# Neuron Audio Router

Fleet service + graphical audio routing for EVS Neuron IPG units, live over
ACP2 (read-only). **Operator / install documentation: [README.txt](README.txt).**

```
python3 router/server.py                      # http://localhost:8101/
python3 -m unittest discover -s router/tests -p "test_*.py"
```

Developer map:

| file | role |
|---|---|
| `server.py` | service: STATE + health poll loop, `/api/state`, device access guard (`poll`), routing read / channel check jobs, Take/Undo refusal |
| `dashboard.html` | fleet table + router (assisted / manual, Active / Preset), inline JS/CSS |
| `../acp2/client.py`, `../acp2/walk.py` | read-only AN2/ACP2 client and pruned routing walk |
| `cbf_model.py` / `live_model.py` | routing model from a .cbf export / from an ACP2 snapshot (same structure) |
| `checks.py` | per-channel Case A / B analysis, templates (Main and Backup resolved separately) |
| `live_check.py` | fresh read-only ACP2 + NMOS check of one channel |
| `route_lists.py` | Cerebrum SRC/DST lists -> names, channel rows, device index |
| `tests/` | `fake_neuron.py` (simulated device), `test_offline.py`, `load_test.py`, `check_js.py`, `check_channel_live.py` |

Wire format of ACP2/AN2 and what was verified live: `../acp2/README.md`.
Routing rules (Case A / B, delay groups, IP-out gap): `../docs/routing-architecture.md`.
