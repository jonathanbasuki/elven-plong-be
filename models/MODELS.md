# Model weights

Not committed (see `.gitignore`). Run `make models` to download `yolo26n.pt` and
`yolo26l.pt` here via Ultralytics' own auto-download.

The worker loads `--model`, which defaults to `YOLO_WEIGHTS` and then to
`models/yolo26n.pt`. `.env.example` sets `YOLO_WEIGHTS=models/yolo26l.pt`, so
a `.env` copied from it uses the larger model.
