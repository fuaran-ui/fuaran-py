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

## Unreleased (the next minor after 0.10.0)

### behavioural — a field at its declared default is omitted from the wire (Phase 2300, rule 1)

- Fifteen members the vocabulary moved to omit-at-default now write nothing when they hold their
  declared default, and the decoder restores the default when they are absent: `Flex.wrap`
  (`False`), `Link.download` (`False`), `List.ordered` (`False`), `CodeBlock.line_numbers`
  (`False`), `CodeBlock.copyable` (`True`), `CodeBlock.highlight_lines` (`()`),
  `FormField.required` (`False`), `FileUpload.multiple` (`False`), `Modal.dismissable` (`True`),
  `Heading.variant` (`"Standard"`), `Image.variant` (`"Default"`), `Badge.variant` (`"Neutral"`),
  `Button.variant` (`"Secondary"`), `SegmentedChoice.orientation` (`"Horizontal"`) and
  `Stepper.active_step` (`Static(0)`). The same call encodes shorter bytes; an explicit default on
  the wire is still read, and normalises to the omitted form (`lenient-2300-declared-default-*`).
  The coverage pin's `AUTHORING_DEFAULTS` no longer names these members: the authoring default
  and the wire default are now the same value.

### breaking — a multi-select carries `values` and no `value` (Phase 1962)

- `Select.value` is `Binding | None` (default `None`). `fuaran.select(..., multiple=True)` passes no
  `value`; a single-select without one takes `Static(None)` as before, so an existing single-select
  call encodes the same bytes. A single-select document without `value` is refused
  (`MISSING_FIELD` at `$.kind.value`); a multi-select document carrying one is read and the
  `value` dropped (`lenient-1962-multiselect-value-dropped`). `Select.multiple` is an ordinary
  optional member, written as authored (`nodes/select-multiple-false.json`). A consumer that read
  `Select.value` as always present handles `None`.

### additive — the gaps the coverage pin declared are authorable (Phase 2334)

- `ColumnWidth` takes `pixels` (a `Fixed` width) and `weight` (a `Flex` width).
- `Filter` takes `default_value`, as `State` and `Selection` do; `binding.filter(name, default_value=…)`.
- `OnSubmit` and `OnCommitAction` join `LocalFlushTrigger`, and `CommitLocal(node_id)` joins `Action`
  (`action.commit_local(node_id)`): a buffer whose trigger is `OnCommitAction` flushes when a
  `CommitLocal` names its node.
- The handler flags `Chart.on_point_click`, `DataGrid.on_row_click` and `Map.on_marker_click` (each
  default `False`, omitted; `True` writes the closure sentinel), with the same keyword on
  `fuaran.chart` / `fuaran.grid` / `fuaran.map`; `ReadFileBody.on_read` (default `True`, what it
  always wrote; `False` omits it), with the same keyword on `action.read_file_body`.

### breaking — a `Fixed` or `Flex` width needs its member (Phase 2334)

- `ColumnWidth("Fixed")` without `pixels`, or `ColumnWidth("Flex")` without `weight`, raises
  `ValueError`, and so does either member on a case that does not declare it. Each wrote a width
  the wire requires the member on, which a decoder refuses.

### behavioural — a row feed's `None` and nested cells follow WIRE_FORMAT.md §5 (Phase 2334)

- A `DataGrid` / `Chart` row cell that is `None` omits its key, where it wrote `null`, and a cell
  outside rule 11's scalars (a list, a dict, any other object) writes `"<opaque>"`, where it was
  written structurally or raised `TypeError`. Both are what every decoder normalises the cell to;
  `encode_rows` follows the same rule.

## 0.10.0

### breaking — a DataGrid / Chart row cell outside ±2^53, or a naive datetime, is refused (Phase 1960)

- A row feed's (`Chart.source`, `DataGrid.source`) `int` cell outside ±2^53 raises
  `fuaran_ui.schema.types.RowCellRefused` (code `ROW_CELL_INEXACT_INTEGER`, with `row` and `key`) where it was
  silently written as the nearest double, a different number. A naive `datetime` cell raises it with code
  `ROW_CELL_UNSPECIFIED_DATETIME`: it names no instant (WIRE_FORMAT.md §5 "The two encode refusals").
- **behavioural:** an aware `datetime` row cell encodes as its Unix seconds (rule 11), where it raised a plain
  `TypeError`.
- `encode_rows(rows)` is the new function that gives a row feed's canonical bytes, as a `source` writes them.

Nothing else moves: the checks run on the `Static` value and the `State` default of a `source` (through a
`Local`'s `initial_from`), and every other cell lowers as before. The `row-encode/` corpus family is the
oracle, run by `tests/test_row_encode.py`.

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
