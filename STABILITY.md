# fuaran-ui (Python) stability record

This file records changes to the public authoring surface of the `fuaran-ui` Python package
(`fuaran_ui.schema.types`, `fuaran_ui.ui` and the modules it re-exports), classed by what adopting
them costs a consumer:

- **additive** — new parameters, records or aliases; every existing call keeps compiling and
  encodes the same bytes;
- **behavioural** — the same call encodes different bytes;
- **breaking** — an existing call stops working.

The package is pre-1.0, so a minor version may break; this record is how a consumer finds out which
ones did. The wire format itself is governed by the shared specification and its conformance
corpus, not by this file.

## Unreleased (the next minor after 0.9.0)

### additive — the authoring surface covers every generated field (Phase 2316)

- `ColumnKind` takes `label` and `on_click` for a `Button` cell, and `buttons` for a `ButtonGroup`
  cell; each is refused on any other cell kind. `ButtonGroupItem(label, on_click=False)` is the new
  record a `ButtonGroup` cell's buttons are. (fuaran#1909, carried by fuaran#2025.)
- `Column` takes `value` (default `True`, the existing behaviour). `False` omits the erased
  `"value":"<closure>"` projection, which is how a row-action column reads on the wire.
- `TransformBinding.source` admits a live binding — `State`, `Selection` or `Query` — beside a
  `DataSource`; `fuaran_ui.ui.compute.TransformSource` names the union. A `State` source's
  `default_value` rows are the initial snapshot. (fuaran#1100, carried by fuaran#2025.)
- `fuaran_ui.schema.authoring_dialect` declares where the authoring surface deliberately differs
  from the generated wire layer; `tests/test_authoring_coverage.py` holds the two together.

No existing constructor changed: every call that worked on 0.9.0 encodes the same bytes.
