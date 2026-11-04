# Evidence sample (sanitized)

Public-scope exports of genuine runs, produced by `dashboard/analysis.py` (analysis v7). Transformations applied to every file:

* addresses, ports, endpoints and URLs: `[redacted]` or `[ip]`;
* device serial and model: `[redacted]`; local paths: `[path]`;
* everything else (IDs, events, outcomes, phone-monotonic times) is copied unchanged.

Each file maps to private originals through a mapping file that is **not** included.

| File | Test | Final status |
|---|---|---|
| `run-cf710e48-9342-47c4-aa44-f67d16d811e5.public.json` | wifi_connectivity | passed |
| `run-5a42a6a2-f5cb-4464-b9c2-8b493ad47f19.public.json` | usb_connectivity | passed |
| `run-e8822050-8394-4e56-91e6-46caed073652.public.json` | app_restart | passed |
| `run-fa3014fa-d33a-4a35-bbb3-57c28471a04d.public.json` | wifi_sequence | passed |
| `run-f00ad06f-d23b-43c0-acd9-9463c2fe495e.public.json` | wifi_reconnect | inconclusive |
| `run-4332f30e-ac6f-40c5-8de3-037bf899f7bc.public.json` | wifi_reconnect | passed |
| `run-22516f79-dcd1-4994-9918-f2e3a4c9a717.public.json` | wifi_observation | passed |
| `run-29016b2c-dad9-4db4-a796-1ae4e2768263.public.json` | wifi_observation | passed |
| `run-cde75240-ae69-4223-9aa9-f26b4bef4da3.public.json` | wifi_observation | passed |
| `run-50edfe31-5158-4e03-b2f4-de8fefd73e32.public.json` | wifi_observation | inconclusive |
| `all-runs-summary.public.json` | all runs | final statuses only |
