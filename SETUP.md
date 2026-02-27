# Local Development Setup

## Install

```bash
pip3 install . --break-system-packages
```

## Configuration

Copy the example config and edit it:

```bash
cp config.example.yaml config.yaml
```

See `config.example.yaml` for all available options.

## Usage

```bash
# Preview changes (dry run, the default)
qbt-sync

# Apply changes
qbt-sync --no-dry-run

# Custom config path
qbt-sync -c /path/to/config.yaml

# Verbose output
qbt-sync -v
```

## Docker (build locally)

```bash
docker build -t qbt-sync .

docker run -d --restart unless-stopped \
  -v ./config.yaml:/app/config.yaml:ro \
  --name qbt-sync \
  qbt-sync
```

To do a one-off dry run:

```bash
docker run --rm \
  -v ./config.yaml:/app/config.yaml:ro \
  qbt-sync qbt-sync --dry-run
```

## Cross-platform build (arm64 -> amd64)

If you're on an Apple Silicon Mac (arm64) and need to build for an amd64 server:

```bash
docker buildx build --platform linux/amd64 -t gmcouto/qbt-sync:latest --load .
```

To build and push in one step:

```bash
docker buildx build --platform linux/amd64 -t gmcouto/qbt-sync:latest --push .
```

If `buildx` isn't available, create the builder first:

```bash
docker buildx create --name mybuilder --use
docker buildx inspect --bootstrap
```
