# Object Storage

Object storage stores large test artifacts and result files.

## Stored Artifacts

- JMeter `.jtl` files.
- Engine logs.
- Aggregated result files.
- Error samples.
- Reports.
- Screenshots or browser artifacts.
- Environment snapshots.

## Options

- S3-compatible storage.
- MinIO.
- Cloud provider object storage.
- Enterprise artifact repositories.

## Requirements

- Artifacts shall be linked to run IDs.
- Retention policies shall be configurable.
- Sensitive artifacts shall be access controlled.
- Large files shall not be stored in the configuration database.
