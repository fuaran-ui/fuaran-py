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

### breaking (wire readers) — an `int` column value is a signed 32-bit integer (Phase 2484)

- An embedded `Transform` source's `int` column now refuses a value outside the signed 32-bit range
  (`2147483648`, `-2147483649`) and a fractional value (`2.5`) as `TYPE_MISMATCH`, which the node decoder
  surfaces as `WRONG_TYPE` at the source (WIRE_FORMAT §3.6.28). Before this it accepted any Python `int`. A
  whole value written with a point (`2.0`) is now read as the integer it denotes, where it was refused.
- Inference reads a number by value: a whole value inside the range is an `int`, anything else a `float`, so a
  schema-less column holding a whole value past the range infers `float`.
- The authoring surface writes the same columns: `frame(...)` infers `float` for whole values past the range,
  and a declared `int` column given one raises `ValueError` rather than emitting a document its readers refuse.
- Certified by the corpus vectors `grid-transform-int-bounds`, `reject-transform-int-*` and
  `lenient-transform-int-*`, and by `tests/test_int32_column.py`.

### behavioural — the dataframe evaluator propagates and checks units (Phase 2208)

- Every verb carries the whole schema entry: a projection, filter, sort, limit, distinct, both sides of a
  join, a group key, a pivot index and an unpivot id keep `unit`, `label`, `description` and `ext`; a
  derive, an aggregate, a window column, a pivot value and the unpivot's `value` carry the unit the check
  derives (a count and a ranking none) and no label. Before this, every verb answered fresh `(name, type)`
  entries, so a result table lost the metadata its input stated.
- Units are checked over a step's schema before any row: addition, subtraction, comparison, `Coalesce`,
  `Case` arms, `least` / `greatest`, `InList` and a join's key pairs require one unit among the operands that
  state one; products, quotients and integer-literal powers compose through the algebra; two stated units
  that are not one unit (`km` beside `m` included) are refused as `UNIT_MISMATCH`, as are two in a union or a
  set op's column pair. A pipeline over unitless columns answers exactly as it did.
- `fuaran_ui.dataframe` gains `Convert(expr, unit)` (wire `{"$type":"convert",…}`): a tag over an operand
  stating no unit, the float times the exact conversion factor over a compatible one, refused across
  dimensions; and the scalar functions `least` / `greatest`. `fuaran_ui.dataframe.unit` gains `mul_unit`,
  `div_unit`, `pow_unit`, `compatible_units`, the exact `conversion_factor` and `ratio_to_float`.
- Certified by the corpus's `laws/transformLaws` family, whose `-unit-` vectors now run with every other, in
  `tests/test_transform_laws.py` (with a test per verb class and a go-red over the refusals).

### additive — a column's schema entry carries field metadata: unit, label, description, ext (Phase 2207)

- `fuaran_ui.dataframe` gains `Field(name, type, meta=FieldMeta())` and `FieldMeta(unit=None, label=None,
  description=None, ext={})`, and `Table` gains `metadata`, the `FieldMeta` of each column that states any,
  by name (default empty, so every existing table keeps its bytes). `table_fields` / `table_of_fields` read
  and build a table's schema as fields.
- The embedded-source codec reads and writes the four members (WIRE_FORMAT §3.6.26): only the members a
  field states are written, an empty `ext` is none, a member the entry does not define is read past, and a
  `unit` is parsed by the unit algebra and re-encoded as its canonical text (`s-1.m` reads `m/s`, `l` reads
  `L`). A unit that does not parse, a non-string `unit` / `label` / `description`, an `ext` that is not an
  object and a non-string `ext` member are refused as `MALFORMED_SHAPE` naming the column. `encode_field`,
  `decode_field` and `decode_schema` expose one entry and one schema array.
- `fuaran_ui.dataframe.unit` is the unit algebra's text (`parse_unit`, `render_unit`, `UnitOfMeasure`,
  `UnitRefusal` with the reference's refusal classes); `fuaran_ui.dataframe.schema` carries the schema
  operations that read the metadata — `diff`, `patch`, `encode_delta` (with `amended` only when a member
  moved), `classify` (a depended-on unit replaced or withdrawn is breaking, a moved `ext` member unknown) and
  `fingerprint` (FNV-1a over UTF-16 code units; a schema without metadata keeps its fingerprint).
- Certified by the corpus's `laws/fieldLaws` family (43 vectors, kit version 0.41.0) in
  `tests/test_field_laws.py`; the capability manifest declares it covered.

### additive — a Metric can carry a second comparison (Phase 2472)

- `Metric` gains `comparisons`, a tuple of `MetricComparison(label, value, format=FormatNone(),
  polarity="HigherIsBetter")`, omitted on the wire when empty and, inside an entry, at each default — so
  every existing document keeps its bytes. The decoder restores the empty tuple and drops an explicit
  default; an entry missing `label` or `value` is refused.
- The server renderer draws each comparison as one more `fuaran-metric-trend` line after the primary trend:
  its own glyph and sentiment under its OWN polarity, then the figure in its format, then its label. The
  speech projection speaks each after the trend. Both are byte-identical to the reference host on the
  corpus's `metric-two-comparisons` and `metric-comparison-inverted` fixtures.
- `fuaran.metric(..., comparisons=...)` and `quick.metric(..., comparisons=...)` author it. The bundled
  snapshot, the generated structural layer, the capability manifest and the speech golden (regenerated
  from the reference's 0.98.0 build; only the two new fixtures' entries move) are re-synced.

### additive and behavioural — the dataframe evaluator learns `pow`, `exp`, `ln` and `log10`, and refuses an output named twice (Phase 2400)

- `fuaran_ui.dataframe` decodes, encodes and evaluates the four scalar functions the reference added (its
  D43). An integer exponent runs one pinned routine, binary exponentiation over IEEE-754 multiplication
  with one reciprocal for a negative exponent (null where that power is zero), so it is byte-identical on
  every host; an int base under a non-negative int exponent answers an int, refused outside the int32 band.
  A float exponent, `exp`, `ln` and `log10` answer a special-value table first, then `math` (where
  `math.pow` raises `OverflowError`, the answer is the infinity IEEE-754 gives), and agree with the other
  hosts within 2 ulp. A pipeline naming any of the four used to be refused at decode.
- **behavioural:** `project`, `groupBy`, `unpivot` and `pivot` refuse an output that names a column twice
  (the reference's D35), where they used to answer a table with two columns of one name.
- Certified by the corpus's `transformLaws` family at kit version 0.41.0, whose runner now reads
  `expected.tolerance`; the bundled snapshot is re-synced. The authoring surface is unchanged.

### breaking — two number tokens are refused at decode (Phase 2286)

- Every wire reader (`load_bounded`, so `decode_node`, `decode_op` and the secondary readers) refuses
  two number tokens it used to read, both as `INVALID_JSON`: an overflowing exponent (`1e999`),
  which read as an infinity (WIRE_FORMAT.md §20.2 row 7, amended), and an integer token past 2^53
  that is not the canonical float layout of the double it reads as (`9007199254740993`, a 19-digit
  identifier), which read as the nearest double (§2 rule 5, amended). `1e-999` still reads as zero
  and `10000000000000000`, a canonical layout, still reads. Neither refused token can be written by
  a conformant encoder; the corpus moves with the amendment (`reject-json-number-overflow-exponent`,
  `reject-json-integer-beyond-int53`; `lenient-1521-payload-integer-beyond-int53` leaves), and the
  bundled snapshot is re-synced. A consumer carrying an integer that can leave ±2^53 carries it as a
  string. The authoring surface is unchanged.

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

### breaking — the dataframe algebra's closed unions gain the exact decimal (Phase 2001)

- `fuaran_ui.dataframe`: the column-type set gains `decimal` (`DECIMAL`), whose cell carries its value
  as canonical decimal TEXT (`cell_decimal`); `ColExpr` gains `Quotient(dividend, divisor, rounding)`
  and `Rounded(expr, rounding)` over the new `Rounding(scale, mode)` record (the seven modes in
  `ROUNDING_MODES`); `Transform` gains `Intersect(source)` and `Except(source)`; the aggregate set
  gains `countDistinct`. Code that matches every case of one of these unions must handle the new ones.
- `fuaran_ui.dataframe.decimal_text` is the decimal's grammar, order, exact arithmetic and the one
  rounding kernel; `try_encode_source`, `aggregate` and `cell_token` are new beside the codec and the
  evaluator.

### behavioural — the evaluator follows the reference on typing, ordering and aggregates (Phase 2001)

- A derived column is typed by its expression wherever the schema decides it — on an empty and an
  all-null frame too — and by its cells (their widening join) only where it does not; a float beside
  a decimal, or two cell types no column holds together, is refused by name. An `unpivot` value
  column is typed by its value columns' declared types the same way.
- NaN orders above every other float; grouping, distinct and window partitions key on the canonical
  cell token; float sums and means fold left to right; `sum` / `mean` / `median` / `stddev` over a
  non-numeric column is refused rather than answering null.

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
