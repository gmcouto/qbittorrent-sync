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
