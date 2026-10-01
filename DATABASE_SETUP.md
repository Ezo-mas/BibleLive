# Database Setup Guide

## Overview

BibleLive uses SQLite for Bible text storage. Database import is now an **explicit one-time setup step**, separated from runtime operation.

## Quick Setup

### 1. Import Bible Data

```bash
# Import KJV translation (default)
python3 -m core.import_bible --translation kjv --source data/kjv.json

# Or specify custom output location
python3 -m core.import_bible --translation kjv --source data/kjv.json --output bible_data/custom.sqlite
```

### 2. Verify Import

```bash
python3 -m core.import_bible --output bible_data/biblelive.sqlite --info
```

Expected output:
```
Database Information:
============================================================
Translation:      kjv
Source:           kjv.json
Import Date:      2026-09-30T19:51:28.123456
Version:          1.0
Books:            66
Verses:           31102
Database Size:    4.85 MB
============================================================
```

### 3. Start Server

```bash
./start.sh
```

The server will use the existing database without reimporting.

## Database Location

**Default:** `bible_data/biblelive.sqlite`

**Override:** Set `BIBLELIVE_DB_PATH` environment variable

```bash
export BIBLELIVE_DB_PATH=bible_data/custom.sqlite
./start.sh
```

## Import Command Reference

### Basic Import
```bash
python3 -m core.import_bible --translation kjv --source data/kjv.json
```

### Force Reimport
```bash
python3 -m core.import_bible --translation kjv --source data/kjv.json --force
```

### Show Database Info
```bash
python3 -m core.import_bible --output bible_data/biblelive.sqlite --info
```

### Custom Output Location
```bash
python3 -m core.import_bible \
  --translation kjv \
  --source data/kjv.json \
  --output bible_data/kjv_production.sqlite
```

## Database Schema

### Tables

**books**
- `id` - Primary key
- `name` - Full book name (e.g., "Genesis", "Matthew")
- `testament` - "Old Testament" or "New Testament"
- `order_index` - Canonical order

**verses**
- `id` - Auto-increment primary key
- `book_id` - Foreign key to books
- `chapter` - Chapter number
- `verse` - Verse number
- `text` - Verse text

**bible_metadata**
- `key` - Metadata key
- `value` - Metadata value

### Metadata Fields

- `translation` - Translation identifier (e.g., "kjv")
- `source_file` - Original JSON filename
- `import_timestamp` - ISO 8601 timestamp
- `version` - Schema version

### Indexes

- `idx_verses_lookup` - Fast lookup by (book_id, chapter, verse)
- `idx_books_name` - Fast lookup by book name

## Production Deployment

### Read-Only Mode

Set `BIBLELIVE_DB_READONLY=true` in production to prevent accidental modifications:

```bash
export BIBLELIVE_DB_READONLY=true
./start.sh
```

### Pre-Import Database

1. Import database during deployment setup:
   ```bash
   python3 -m core.import_bible --translation kjv --source data/kjv.json
   ```

2. Verify import:
   ```bash
   python3 -m core.import_bible --output bible_data/biblelive.sqlite --info
   ```

3. Start server (will use existing database):
   ```bash
   ./start.sh
   ```

### Docker/Container

In Dockerfile:
```dockerfile
# Copy source data
COPY data/kjv.json /app/data/

# Import during build
RUN python3 -m core.import_bible --translation kjv --source data/kjv.json

# Runtime uses existing database
CMD ["./start.sh"]
```

Environment variables:
```yaml
environment:
  BIBLELIVE_DB_PATH: bible_data/biblelive.sqlite
  BIBLELIVE_DB_READONLY: true
```

## Multi-Translation Support (Future)

The import command supports multiple translations:

```bash
# Import KJV
python3 -m core.import_bible --translation kjv --source data/kjv.json --output bible_data/kjv.sqlite

# Import NIV (when available)
python3 -m core.import_bible --translation niv --source data/niv.json --output bible_data/niv.sqlite
```

Configure which database to use:
```bash
export BIBLELIVE_DB_PATH=bible_data/niv.sqlite
```

## Troubleshooting

### Import Failed

Check that source JSON exists and is valid:
```bash
ls -lh data/kjv.json
python3 -c "import json; json.load(open('data/kjv.json'))"
```

### Database Already Exists

Use `--force` to reimport:
```bash
python3 -m core.import_bible --translation kjv --source data/kjv.json --force
```

### Server Can't Find Database

Check path configuration:
```bash
echo $BIBLELIVE_DB_PATH
ls -lh bible_data/biblelive.sqlite
```

### Permission Issues

Ensure database directory is writable (if not using readonly mode):
```bash
chmod 755 bible_data
chmod 644 bible_data/biblelive.sqlite
```

## Migration from Old System

If you have an existing database without metadata:

1. Run import with `--force` to add metadata table:
   ```bash
   python3 -m core.import_bible --translation kjv --source data/kjv.json --force
   ```

2. Or manually add metadata:
   ```sql
   CREATE TABLE IF NOT EXISTS bible_metadata (key TEXT PRIMARY KEY, value TEXT);
   INSERT INTO bible_metadata VALUES ('translation', 'kjv');
   INSERT INTO bible_metadata VALUES ('import_timestamp', datetime('now'));
   ```

## Git and Version Control

Generated databases are in `.gitignore`. Only commit:
- Source JSON files (`data/kjv.json`)
- Import scripts
- Schema documentation

Do not commit:
- `bible_data/*.sqlite` - Generated databases
- `bible_data/*.db` - Test databases
