// Regenerates reference-scalar-fns.json: the REFERENCE evaluator's own answer for every vector below.
//
//     dotnet fsi generate_reference.fsx            (writes reference-scalar-fns.json beside this file)
//
// The vectors exercise every scalar function the ColExpr wire admits, one block per function, and
// each answer is computed by the published reference package, never written by hand. A float result
// is recorded as its IEEE-754 bit pattern so the comparison is exact rather than a text round-trip.
#r "nuget: Fuaran.Compute.DataFrame, 0.47.0"

open System
open System.IO
open System.Text.Json.Nodes
open Fuaran.Core
open Fuaran.Compute

let producer = "Fuaran.Compute.DataFrame 0.47.0"

let s (v: string) = Str v
let i (v: int) = Int v
let f (v: float) = Float v
let d (v: string) = Date v
let ts (v: string) = Timestamp v
let dec (v: string) = Decimal v

let vectors: (string * ScalarFn * Cell list) list =
    [ // trim: the pinned ASCII set, nothing wider.
      "trim-ascii-set", Trim, [ s " \t\r\n a b \n\r\t " ]
      "trim-keeps-nbsp", Trim, [ s " x " ]
      "trim-keeps-nel", Trim, [ s "\u0085x\u0085" ]
      "trim-keeps-vt-ff", Trim, [ s "\u000bx\u000c" ]
      "trim-keeps-ideographic-space", Trim, [ s "　x　" ]
      "trim-empty", Trim, [ s "" ]
      "trim-all-space", Trim, [ s "  \t " ]
      "trim-null", Trim, [ Null ]
      "trim-int", Trim, [ i 3 ]
      "trim-date", Trim, [ d "2024-01-01" ]
      "trim-arity-0", Trim, []
      "trim-arity-2", Trim, [ s "a"; s "b" ]
      // replace: every occurrence, left to right, non-overlapping; an empty find is the subject.
      "replace-all", Replace, [ s "aXbXc"; s "X"; s "-" ]
      "replace-non-overlapping", Replace, [ s "aaaa"; s "aa"; s "a" ]
      "replace-to-empty", Replace, [ s "abab"; s "ab"; s "" ]
      "replace-empty-find", Replace, [ s "abc"; s ""; s "x" ]
      "replace-absent", Replace, [ s "abc"; s "z"; s "x" ]
      "replace-ordinal-case", Replace, [ s "Aa"; s "a"; s "b" ]
      "replace-astral", Replace, [ s "a\U0001F600b"; s "\U0001F600"; s "c" ]
      "replace-null-subject", Replace, [ Null; s "a"; s "b" ]
      "replace-null-find", Replace, [ s "a"; Null; s "b" ]
      "replace-null-repl", Replace, [ s "a"; s "a"; Null ]
      "replace-null-beats-type", Replace, [ i 1; Null; s "b" ]
      "replace-int-subject", Replace, [ i 1; s "a"; s "b" ]
      "replace-int-repl", Replace, [ s "a"; s "a"; i 1 ]
      "replace-arity-2", Replace, [ s "a"; s "a" ]
      // dateDiffDays: second minus first, over the leading YYYY-MM-DD.
      "ddd-forward", DateDiffDays, [ d "2024-01-01"; d "2024-03-01" ]
      "ddd-backward", DateDiffDays, [ d "2024-03-01"; d "2024-01-01" ]
      "ddd-same", DateDiffDays, [ d "2024-02-29"; d "2024-02-29" ]
      "ddd-leap-day", DateDiffDays, [ s "2023-02-28"; s "2024-02-28" ]
      "ddd-timestamp-head", DateDiffDays, [ ts "2024-02-29T23:59:59Z"; d "2024-03-01" ]
      "ddd-string-head", DateDiffDays, [ s "2024-01-01T00:00:00Z"; s "2024-01-02 anything" ]
      "ddd-epoch-span", DateDiffDays, [ d "0000-01-01"; d "9999-12-31" ]
      "ddd-pre-epoch", DateDiffDays, [ d "1969-12-31"; d "1970-01-01" ]
      // The corpus's two `Now` fixtures (`nodes/now-grain.json`, `nodes/now-environment-binding.json`)
      // at the instant `render-text.json` pins, `2026-08-02T06:59:24Z`: Day grain, then no grain.
      "ddd-now-grain-inv-2001", DateDiffDays, [ s "2026-08-02"; s "2026-07-01" ]
      "ddd-now-env-inv-1001", DateDiffDays, [ s "2026-08-02T06:59:24Z"; s "2026-07-01" ]
      "ddd-now-env-inv-1002", DateDiffDays, [ s "2026-08-02T06:59:24Z"; s "2026-07-28" ]
      "ddd-invalid-day", DateDiffDays, [ s "2023-02-29"; d "2024-01-01" ]
      "ddd-month-13", DateDiffDays, [ d "2024-01-01"; s "2024-13-01" ]
      "ddd-short", DateDiffDays, [ s "2024-1-01"; d "2024-01-01" ]
      "ddd-nine-chars", DateDiffDays, [ s "2024-01-0"; d "2024-01-01" ]
      "ddd-empty", DateDiffDays, [ s ""; d "2024-01-01" ]
      "ddd-slash", DateDiffDays, [ s "2024/01/01"; d "2024-01-01" ]
      "ddd-non-ascii-digit", DateDiffDays, [ s "２024-01-01"; d "2024-01-01" ]
      "ddd-first-error-wins", DateDiffDays, [ s "bad"; i 1 ]
      "ddd-int", DateDiffDays, [ d "2024-01-01"; i 5 ]
      "ddd-null-first", DateDiffDays, [ Null; d "2024-01-01" ]
      "ddd-null-second", DateDiffDays, [ d "2024-01-01"; Null ]
      "ddd-null-beats-type", DateDiffDays, [ i 5; Null ]
      "ddd-arity-1", DateDiffDays, [ d "2024-01-01" ]
      // sqrt: a float; a negative root is null; a decimal refused by name.
      "sqrt-int", Sqrt, [ i 2 ]
      "sqrt-float", Sqrt, [ f 6.25 ]
      "sqrt-zero", Sqrt, [ f 0.0 ]
      "sqrt-negative-zero", Sqrt, [ f -0.0 ]
      "sqrt-negative", Sqrt, [ i -1 ]
      "sqrt-large", Sqrt, [ f 1e308 ]
      "sqrt-decimal", Sqrt, [ dec "4" ]
      "sqrt-string", Sqrt, [ s "4" ]
      "sqrt-null", Sqrt, [ Null ]
      "sqrt-arity-2", Sqrt, [ i 1; i 2 ]
      // indexOf: ordinal, 0-based UTF-16 index, -1 when absent.
      "indexof-found", IndexOf, [ s "hello"; s "l" ]
      "indexof-absent", IndexOf, [ s "hello"; s "z" ]
      "indexof-empty-needle", IndexOf, [ s "hello"; s "" ]
      "indexof-both-empty", IndexOf, [ s ""; s "" ]
      "indexof-ordinal-case", IndexOf, [ s "Hello"; s "h" ]
      "indexof-after-astral", IndexOf, [ s "\U0001F600a"; s "a" ]
      "indexof-null", IndexOf, [ Null; s "a" ]
      "indexof-null-needle", IndexOf, [ s "a"; Null ]
      "indexof-int", IndexOf, [ i 1; s "a" ]
      "indexof-arity-1", IndexOf, [ s "a" ]
      // the functions this host already evaluated: one vector each, plus the text edges.
      "abs-int", Abs, [ i -3 ]
      "round-half-away", Round, [ f -2.5 ]
      "floor-float", Floor, [ f -1.5 ]
      "ceil-float", Ceil, [ f 1.2 ]
      "length-ascii", Length, [ s "abc" ]
      "length-astral", Length, [ s "a\U0001F600" ]
      "lower-ascii", Lower, [ s "AbC" ]
      "upper-ascii", Upper, [ s "aBc" ]
      "upper-sharp-s", Upper, [ s "straße" ]
      "lower-dotted-capital-i", Lower, [ s "İ" ]
      "substr-mid", Substr, [ s "hello"; i 1; i 3 ]
      "substr-after-astral", Substr, [ s "\U0001F600ab"; i 2; i 1 ]
      "substr-date-subject", Substr, [ d "2024-01-01"; i 0; i 4 ]
      "substr-bool-start", Substr, [ s "hello"; Bool true; i 1 ]
      "replace-date-subject", Replace, [ d "2024-01-01"; s "-"; s "/" ]
      "indexof-date-subject", IndexOf, [ d "2024-01-01"; s "-" ]
      "length-date", Length, [ d "2024-01-01" ]
      "datepart-year", DatePart, [ s "year"; d "2024-02-29" ]
      "concat-mixed", Concat, [ s "n="; i 3 ]
      "least-ints", Least, [ i 3; i 1; i 2 ]
      "greatest-strs", Greatest, [ s "a"; s "c"; s "b" ]
      "pow-int", Pow, [ i 2; i 10 ]
      "exp-zero", Exp, [ i 0 ]
      "ln-one", Ln, [ i 1 ]
      "log10-hundred", Log10, [ i 100 ] ]

// The string predicates are binary operators rather than functions, and the same contract: the
// ColExpr wire admits them, so a host evaluates them.
let predicates: (string * BinOp * Cell * Cell) list =
    [ "contains-found", Contains, s "hello", s "ell"
      "contains-absent", Contains, s "hello", s "z"
      "contains-empty", Contains, s "hello", s ""
      "contains-ordinal-case", Contains, s "Hello", s "h"
      "startswith-found", StartsWith, s "hello", s "he"
      "startswith-absent", StartsWith, s "hello", s "lo"
      "startswith-empty", StartsWith, s "", s ""
      "endswith-found", EndsWith, s "hello", s "lo"
      "endswith-absent", EndsWith, s "hello", s "he"
      "contains-null-left", Contains, Null, s "a"
      "startswith-null-right", StartsWith, s "a", Null
      "endswith-null-beats-type", EndsWith, i 1, Null
      "contains-int", Contains, i 1, s "1"
      "startswith-date", StartsWith, d "2024-01-01", s "2024" ]

let cellJson (c: Cell) : JsonNode =
    let pair (tag: string) (v: JsonNode) =
        JsonArray(JsonValue.Create tag :> JsonNode, v) :> JsonNode

    match c with
    | Null -> JsonArray(JsonValue.Create "null" :> JsonNode) :> JsonNode
    | Int v -> pair "int" (JsonValue.Create v)
    | Float v -> pair "float" (JsonValue.Create(BitConverter.DoubleToInt64Bits(v).ToString("x16")))
    | Bool v -> pair "bool" (JsonValue.Create v)
    | Str v -> pair "str" (JsonValue.Create v)
    | Date v -> pair "date" (JsonValue.Create v)
    | Timestamp v -> pair "timestamp" (JsonValue.Create v)
    | Decimal v -> pair "decimal" (JsonValue.Create v)

let errJson (e: EvalError) : JsonNode =
    let o = JsonObject()

    match e with
    | TypeError detail ->
        o["code"] <- JsonValue.Create "TYPE_ERROR"
        o["detail"] <- JsonValue.Create detail
    | ArityError(_, expected, got) ->
        o["code"] <- JsonValue.Create "ARITY_ERROR"
        o["expected"] <- JsonValue.Create expected
        o["got"] <- JsonValue.Create got
    | other -> o["code"] <- JsonValue.Create(sprintf "%A" other)

    o :> JsonNode

let tag =
    function
    | Abs -> "abs"
    | Round -> "round"
    | Floor -> "floor"
    | Ceil -> "ceil"
    | Length -> "length"
    | Lower -> "lower"
    | Upper -> "upper"
    | Substr -> "substr"
    | DatePart -> "datePart"
    | Concat -> "concat"
    | Trim -> "trim"
    | Replace -> "replace"
    | DateDiffDays -> "dateDiffDays"
    | Sqrt -> "sqrt"
    | Least -> "least"
    | Greatest -> "greatest"
    | IndexOf -> "indexOf"
    | Pow -> "pow"
    | Exp -> "exp"
    | Ln -> "ln"
    | Log10 -> "log10"

let root = JsonObject()
root["producer"] <- JsonValue.Create producer
let arr = JsonArray()

for (id, fn, args) in vectors do
    let o = JsonObject()
    o["id"] <- JsonValue.Create id
    o["fn"] <- JsonValue.Create(tag fn)
    o["args"] <- JsonArray(args |> List.map cellJson |> Array.ofList)

    match DataFrame.evalExprInRow Map.empty [] [] (ApplyFn(fn, args |> List.map Lit)) with
    | Ok c -> o["ok"] <- cellJson c
    | Error e -> o["err"] <- errJson e

    arr.Add(o)

root["vectors"] <- arr

let opTag =
    function
    | Contains -> "contains"
    | StartsWith -> "startsWith"
    | EndsWith -> "endsWith"
    | other -> failwithf "not a string predicate: %A" other

let preds = JsonArray()

for (id, op, a, b) in predicates do
    let o = JsonObject()
    o["id"] <- JsonValue.Create id
    o["op"] <- JsonValue.Create(opTag op)
    o["args"] <- JsonArray(cellJson a, cellJson b)

    match DataFrame.evalExprInRow Map.empty [] [] (Binary(op, Lit a, Lit b)) with
    | Ok c -> o["ok"] <- cellJson c
    | Error e -> o["err"] <- errJson e

    preds.Add(o)

root["predicates"] <- preds
let opts = System.Text.Json.JsonSerializerOptions(WriteIndented = true)
opts.Encoder <- System.Text.Encodings.Web.JavaScriptEncoder.UnsafeRelaxedJsonEscaping
let out = Path.Combine(__SOURCE_DIRECTORY__, "reference-scalar-fns.json")
File.WriteAllText(out, root.ToJsonString(opts).Replace("\r\n", "\n") + "\n")
printfn "wrote %d vectors and %d predicates to %s" arr.Count preds.Count out
