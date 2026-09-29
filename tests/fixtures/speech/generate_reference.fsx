// Regenerates `reference-speech.json` — the REFERENCE host's speech projection
// (Phase 1813, `Fuaran.UI.Renderer.Server.Speech`) over every node fixture in a
// conformance corpus, so this host's projection (Phase 1913) is checked against
// the reference's own output rather than against a second transcription of it.
//
//     dotnet fsi tests/fixtures/speech/generate_reference.fsx <corpus-root> <out.json>
//
// The package source is the local folder feed beside the workspace checkout
// (`../../../../../../local-nuget-feed` from this file, in the canonical side-by-side
// checkout; a clone at another depth edits the `#i` line locally). Pin the version to the
// reference build whose Speech module the golden records, and regenerate the
// golden whenever that pin moves; the golden's `generator` member names the version
// it was generated from, and `tests/test_speech.py` fails when the bundled corpus
// snapshot gains or loses a node fixture the golden does not cover.

#i "nuget: ../../../../../../local-nuget-feed"
#r "nuget: Fuaran.UI.Renderer.Server, 0.87.0"
#r "nuget: Fuaran.UI.Ops, 0.87.0"

open System
open System.IO
open System.Text.Json
open System.Text.Json.Nodes
open Fuaran.UI.Renderer.Server
open Fuaran.UI.Renderer.Server.Speech

let args = fsi.CommandLineArgs |> Array.skip 1

if args.Length <> 2 then
    failwith "usage: generate_reference.fsx <corpus-root> <out.json>"

let corpusRoot, outPath = args[0], args[1]

let reasonName (r: OmissionReason) =
    match r with
    | OmissionReason.Hidden -> "hidden"
    | OmissionReason.NotVisible -> "not-visible"
    | OmissionReason.Closed -> "closed"
    | OmissionReason.NotTaken -> "not-taken"
    | OmissionReason.InsideAnnounced -> "inside-announced"
    | OmissionReason.Decorative -> "decorative"
    | OmissionReason.NothingSayable -> "nothing-sayable"
    | OmissionReason.DepthExceeded -> "depth-exceeded"
    | OmissionReason.UnresolvedFragment _ -> "unresolved-fragment"

let sourceName (s: UtteranceSource) =
    match s with
    | UtteranceSource.Speak -> "speak"
    | UtteranceSource.Derived -> "derived"
    | UtteranceSource.Announced -> "announced"

let pauseName (p: Pause) =
    match p with
    | Pause.None -> "none"
    | Pause.Short -> "short"
    | Pause.Long -> "long"

let fixtures = JsonObject()

for path in
    Directory.GetFiles(Path.Combine(corpusRoot, "nodes"), "*.json")
    |> Array.sortWith (fun a b -> String.CompareOrdinal(a, b)) do
    match Fuaran.UI.Ops.JsonDecode.decodeNodeObj (File.ReadAllText path) with
    | Error _ -> ()
    | Ok node ->
        let script, omitted = projectStatic node
        let entry = JsonObject()
        entry["text"] <- JsonValue.Create(toPlainText script)
        entry["ssml"] <- JsonValue.Create(toSsml script)

        let utterances = JsonArray()

        for u in script.Utterances do
            let o = JsonObject()
            o["nodeId"] <- JsonValue.Create u.NodeId
            o["source"] <- JsonValue.Create(sourceName u.Source)
            o["emphasis"] <- JsonValue.Create u.Emphasis
            o["pause"] <- JsonValue.Create(pauseName u.PauseAfter)
            utterances.Add o

        entry["utterances"] <- utterances

        let omissions = JsonArray()

        for om in omitted do
            let o = JsonObject()
            o["nodeId"] <- JsonValue.Create om.NodeId
            o["kind"] <- JsonValue.Create om.Kind
            o["reason"] <- JsonValue.Create(reasonName om.Reason)
            o["decidedAt"] <- JsonValue.Create om.DecidedAt
            omissions.Add o

        entry["omissions"] <- omissions
        fixtures[Path.GetFileNameWithoutExtension path] <- entry

let doc = JsonObject()
doc["generator"] <- JsonValue.Create "Fuaran.UI.Renderer.Server 0.87.0 Speech.projectStatic"
doc["fixtures"] <- fixtures

let opts = JsonSerializerOptions(WriteIndented = true)
opts.Encoder <- System.Text.Encodings.Web.JavaScriptEncoder.UnsafeRelaxedJsonEscaping
File.WriteAllText(outPath, doc.ToJsonString(opts).Replace("\r\n", "\n") + "\n")
printfn "wrote %d fixtures to %s" fixtures.Count outPath
