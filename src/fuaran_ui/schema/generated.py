"""GENERATED from ``idl.json`` by ``fuaran_ui/schema/generate.py`` — DO NOT EDIT BY HAND.

Regenerate with ``python -m fuaran_ui.schema.generate`` (or ``python conformance/sync_corpus.py``,
which regenerates after re-syncing the bundled snapshot). The structural vocabulary of the wire
format, as data: what each kind, record, union case and op carries, how optional each member is,
and the default an omitted member means. Behaviour lives in the hand-written modules that read it.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

#: SHA-256 of the exact ``idl.json`` bytes this module was generated from.
IDL_SHA256 = "c78f383ca50dc36dc5d158d63052aa202e70d2a1bdee77a30c629354247f76d4"
IDL_VERSION = 1


@dataclass(frozen=True, slots=True)
class T:
    """One IDL type: ``kind`` is its ``$type``; ``of`` the list element / map value type.

    ``wire`` names a sentinel wire class (``<closure>`` / ``json``); ``wire_type`` the wire type a
    host-surface member carries instead.
    """

    kind: str
    name: str | None = None
    of: T | None = None
    args: tuple[T, ...] = ()
    wire: str | None = None
    wire_type: T | None = None


@dataclass(frozen=True, slots=True)
class F:
    """One member: ``optionality`` is ``required`` | ``optional`` | ``omitDefault`` | ``hostOnly``."""

    name: str
    optionality: str
    type: T
    default: object = None

    @property
    def required(self) -> bool:
        return self.optionality == "required"

    @property
    def on_wire(self) -> bool:
        """False for a ``hostOnly`` member, which carries nothing observable on the wire."""
        return self.optionality != "hostOnly"


# ── Types (each distinct IDL type once; nested types by name) ──────────────

T_union_TextSource = T("union", "TextSource")
T_enum_BadgeVariant = T("enum", "BadgeVariant")
T_node = T("node")
T_list_node = T("list", of=T_node)
T_union_BoxLayout = T("union", "BoxLayout")
T_enum_BoxRole = T("enum", "BoxRole")
T_bool = T("bool")
T_union_Action = T("union", "Action")
T_enum_ButtonVariant = T("enum", "ButtonVariant")
T_str = T("str")
T_fn = T("fn", wire="<closure>")
T_union_Binding_bool = T("union", "Binding", args=(T_bool,))
T_enum_ToneVariant = T("enum", "ToneVariant")
T_enum_ChartKind = T("enum", "ChartKind")
T_hosted_json = T("hosted", wire="json")
T_union_Binding_hosted_json = T("union", "Binding", args=(T_hosted_json,))
T_list_str = T("list", of=T_str)
T_union_Format = T("union", "Format")
T_enum_ChartLegendPosition = T("enum", "ChartLegendPosition")
T_enum_ChartDataLabels = T("enum", "ChartDataLabels")
T_enum_ChartXScale = T("enum", "ChartXScale")
T_union_ChartAnnotation = T("union", "ChartAnnotation")
T_list_union_ChartAnnotation = T("list", of=T_union_ChartAnnotation)
T_int = T("int")
T_list_int = T("list", of=T_int)
T_json = T("json")
T_map_json = T("map", of=T_json)
T_record_ContentHash = T("record", "ContentHash")
T_record_ColumnErased = T("record", "ColumnErased")
T_list_record_ColumnErased = T("list", of=T_record_ColumnErased)
T_record_DefaultSort = T("record", "DefaultSort")
T_union_Binding_int = T("union", "Binding", args=(T_int,))
T_record_StaticRows = T("record", "StaticRows")
T_union_Shape = T("union", "Shape")
T_list_union_Shape = T("list", of=T_union_Shape)
T_record_DrawStyle = T("record", "DrawStyle")
T_record_ViewBox = T("record", "ViewBox")
T_enum_ImageAspect = T("enum", "ImageAspect")
T_enum_EmbedPermission = T("enum", "EmbedPermission")
T_list_enum_EmbedPermission = T("list", of=T_enum_EmbedPermission)
T_union_Binding_str = T("union", "Binding", args=(T_str,))
T_enum_CaptureSource = T("enum", "CaptureSource")
T_record_FilterSpec = T("record", "FilterSpec")
T_list_record_FilterSpec = T("list", of=T_record_FilterSpec)
T_record_FormField = T("record", "FormField")
T_list_record_FormField = T("list", of=T_record_FormField)
T_union_HoleDecl = T("union", "HoleDecl")
T_list_union_HoleDecl = T("list", of=T_union_HoleDecl)
T_record_EffectClass = T("record", "EffectClass")
T_union_FragmentArg = T("union", "FragmentArg")
T_map_union_FragmentArg = T("map", of=T_union_FragmentArg)
T_enum_HeadingVariant = T("enum", "HeadingVariant")
T_enum_IconSize = T("enum", "IconSize")
T_enum_ImageVariant = T("enum", "ImageVariant")
T_enum_ImageFit = T("enum", "ImageFit")
T_enum_ImageLoading = T("enum", "ImageLoading")
T_record_SrcSetEntry = T("record", "SrcSetEntry")
T_list_record_SrcSetEntry = T("list", of=T_record_SrcSetEntry)
T_union_CellFormat = T("union", "CellFormat")
T_float = T("float")
T_union_Binding_float = T("union", "Binding", args=(T_float,))
T_enum_LinkProtection = T("enum", "LinkProtection")
T_list_union_TextSource = T("list", of=T_union_TextSource)
T_record_MapMarker = T("record", "MapMarker")
T_list_record_MapMarker = T("list", of=T_record_MapMarker)
T_union_Binding_list_record_MapMarker = T("union", "Binding", args=(T_list_record_MapMarker,))
T_enum_MathDisplay = T("enum", "MathDisplay")
T_union_MediaKind = T("union", "MediaKind")
T_record_TrackEntry = T("record", "TrackEntry")
T_list_record_TrackEntry = T("list", of=T_record_TrackEntry)
T_enum_StyleWeight = T("enum", "StyleWeight")
T_enum_Emphasis = T("enum", "Emphasis")
T_enum_TrendPolarity = T("enum", "TrendPolarity")
T_record_MetricComparison = T("record", "MetricComparison")
T_list_record_MetricComparison = T("list", of=T_record_MetricComparison)
T_enum_ModalityKind = T("enum", "ModalityKind")
T_record_GuestChannel = T("record", "GuestChannel")
T_enum_ScrollOrientation = T("enum", "ScrollOrientation")
T_record_SelectOption = T("record", "SelectOption")
T_list_record_SelectOption = T("list", of=T_record_SelectOption)
T_union_Binding_list_record_SelectOption = T("union", "Binding", args=(T_list_record_SelectOption,))
T_union_Binding_list_str = T("union", "Binding", args=(T_list_str,))
T_list_float = T("list", of=T_float)
T_union_Binding_list_float = T("union", "Binding", args=(T_list_float,))
T_record_SwitchCase = T("record", "SwitchCase")
T_list_record_SwitchCase = T("list", of=T_record_SwitchCase)
T_enum_Orientation = T("enum", "Orientation")
T_record_TabHeader = T("record", "TabHeader")
T_list_record_TabHeader = T("list", of=T_record_TabHeader)
T_record_TreeItem = T("record", "TreeItem")
T_list_record_TreeItem = T("list", of=T_record_TreeItem)
T_record_Accessibility = T("record", "Accessibility")
T_record_StateBehaviour = T("record", "StateBehaviour")
T_record_SemanticStyle = T("record", "SemanticStyle")
T_enum_LiveRegionKind = T("enum", "LiveRegionKind")
T_union_CellKindErased = T("union", "CellKindErased")
T_union_ColumnWidth = T("union", "ColumnWidth")
T_union_Binding_json = T("union", "Binding", args=(T_json,))
T_enum_CompareOp = T("enum", "CompareOp")
T_enum_HashStrictness = T("enum", "HashStrictness")
T_enum_SortDirection = T("enum", "SortDirection")
T_enum_TextAnchor = T("enum", "TextAnchor")
T_enum_DeterminismSource = T("enum", "DeterminismSource")
T_enum_HostEffect = T("enum", "HostEffect")
T_record_CompareRule = T("record", "CompareRule")
T_enum_TextFormat = T("enum", "TextFormat")
T_union_FormFieldKind = T("union", "FormFieldKind")
T_record_FieldRule = T("record", "FieldRule")
T_enum_ChannelDirection = T("enum", "ChannelDirection")
T_enum_TextDirection = T("enum", "TextDirection")
T_enum_StyleRole = T("enum", "StyleRole")
T_enum_FontVoice = T("enum", "FontVoice")
T_list_list_union_TextSource = T("list", of=T_list_union_TextSource)
T_enum_TrackKind = T("enum", "TrackKind")
T_list_union_Action = T("list", of=T_union_Action)
T_record_InvokeArg = T("record", "InvokeArg")
T_list_record_InvokeArg = T("list", of=T_record_InvokeArg)
T_enum_FileReadEncoding = T("enum", "FileReadEncoding")
T_union_CallResultTarget = T("union", "CallResultTarget")
T_enum_NavigateTarget = T("enum", "NavigateTarget")
T_var_T = T("var", "T")
T_enum_TimeGrain = T("enum", "TimeGrain")
T_union_LocalFlushTrigger = T("union", "LocalFlushTrigger")
T_union_Binding_var_T = T("union", "Binding", args=(T_var_T,))
T_union_LocaleSource = T("union", "LocaleSource")
T_map_union_Binding_json = T("map", of=T_union_Binding_json)
T_list_hosted_json = T("list", of=T_hosted_json)
T_record_TransformParam = T("record", "TransformParam")
T_list_record_TransformParam = T("list", of=T_record_TransformParam)
T_enum_DurationUnit = T("enum", "DurationUnit")
T_enum_DurationStyle = T("enum", "DurationStyle")
T_enum_RelativeTimeUnit = T("enum", "RelativeTimeUnit")
T_record_ButtonGroupItem = T("record", "ButtonGroupItem")
T_list_record_ButtonGroupItem = T("list", of=T_record_ButtonGroupItem)
T_map_enum_ToneVariant = T("map", of=T_enum_ToneVariant)
T_union_ChartAnnotationX = T("union", "ChartAnnotationX")
T_union_ChartAnnotationRange = T("union", "ChartAnnotationRange")
T_record_DrawPoint = T("record", "DrawPoint")
T_hosted_as_union_Binding_str = T("hosted", wire_type=T_union_Binding_str)
T_hosted_as_union_Binding_float = T("hosted", wire_type=T_union_Binding_float)
T_hosted_as_union_Binding_bool = T("hosted", wire_type=T_union_Binding_bool)
T_enum_DateTimeVariant = T("enum", "DateTimeVariant")
T_hosted_as_union_Binding_list_str = T("hosted", wire_type=T_union_Binding_list_str)
T_enum_DateStyle = T("enum", "DateStyle")
T_enum_TimeStyle = T("enum", "TimeStyle")
T_union_HoleValueSpace = T("union", "HoleValueSpace")
T_union_Scalar = T("union", "Scalar")
T_list_record_DrawPoint = T("list", of=T_record_DrawPoint)
T_union_CurveCommand = T("union", "CurveCommand")
T_list_union_CurveCommand = T("list", of=T_union_CurveCommand)
T_map_hosted_json = T("map", of=T_hosted_json)
T_op = T("op")
T_list_op = T("list", of=T_op)
T_kind = T("kind")
T_record_StyleDelta = T("record", "StyleDelta")


# ── Enum vocabularies ─────────────────────────────────────────────────────

BadgeVariant = Literal["Neutral", "Brand", "Success", "Warning", "Critical", "Info"]
BoxRole = Literal["Dashboard", "Card", "Group", "Separator"]
ButtonVariant = Literal["Primary", "Secondary", "Tertiary", "Destructive"]
CaptureSource = Literal["Camera", "Microphone"]
ChannelDirection = Literal["OutOnly", "TwoWay"]
ChartDataLabels = Literal["Off", "Ends"]
ChartKind = Literal["Line", "Bar", "Area", "Pie", "Scatter", "Heatmap"]
ChartLegendPosition = Literal["Top", "Right", "Bottom", "None"]
ChartXScale = Literal["Category", "Temporal"]
CompareOp = Literal["eq", "neq", "lt", "lte", "gt", "gte"]
DateStyle = Literal["Short", "Medium", "Long", "Full"]
DateTimeVariant = Literal["Date", "Time", "DateTime"]
DeterminismSource = Literal["Deterministic", "Clock", "Random", "Network"]
DurationStyle = Literal["Compact", "Clock", "Long"]
DurationUnit = Literal["Seconds", "Minutes", "Hours"]
EmbedPermission = Literal["AllowScripts", "AllowSameOrigin", "AllowForms", "AllowFullscreen"]
Emphasis = Literal["Quiet", "Normal", "Loud"]
FileReadEncoding = Literal["Text", "Base64", "DataUrl"]
FontVoice = Literal["Default", "Display", "Structural"]
HashStrictness = Literal["StrictReplay", "AdvisoryWarning", "Enforced"]
HeadingVariant = Literal["Standard", "Eyebrow", "Caption", "Lead"]
HostEffect = Literal["Pure", "ReadsHost", "WritesHost"]
IconSize = Literal["Small", "Medium", "Large"]
ImageAspect = Literal["Natural", "Square", "FourThree", "ThreeTwo", "SixteenNine"]
ImageFit = Literal["Natural", "Cover", "Contain"]
ImageLoading = Literal["Eager", "Lazy"]
ImageVariant = Literal["Default", "Avatar", "Rounded"]
LinkProtection = Literal["email"]
LiveRegionKind = Literal["polite", "assertive", "off"]
MathDisplay = Literal["Inline", "Block"]
ModalityKind = Literal["Modal", "Popover"]
Motion = Literal[
    "None",
    "PulseDuringLoad",
    "FadeInOnMount",
    "SlideInFromBelow",
    "ShakeOnError",
    "RotateOnRefresh",
    "SlideInFromRight",
    "ExpandCollapse",
    "CrossFade",
    "SlideBetween",
]
NavigateTarget = Literal["Self", "Blank"]
Orientation = Literal["Vertical", "Horizontal"]
RelativeTimeUnit = Literal["Second", "Minute", "Hour", "Day", "Week", "Month", "Year"]
ScrollOrientation = Literal["Vertical", "Horizontal", "Both"]
SortDirection = Literal["asc", "desc"]
StyleRole = Literal["None", "Eyebrow", "Data", "Lede", "Caption"]
StyleWeight = Literal["Compact", "Standard", "Spacious"]
TextAnchor = Literal["Start", "Middle", "End"]
TextDirection = Literal["auto", "ltr", "rtl"]
TextFormat = Literal["email", "url", "tel"]
TimeGrain = Literal["Second", "Minute", "Hour", "Day"]
TimeStyle = Literal["Short", "Medium", "Long", "Full"]
ToneVariant = Literal["Default", "Subdued", "Brand", "Success", "Warning", "Critical", "Info"]
TrackKind = Literal["Subtitles", "Captions", "Descriptions", "Chapters"]
TrendPolarity = Literal["HigherIsBetter", "LowerIsBetter"]

#: Every enum's wire cases, in declaration order.
ENUM_CASES: dict[str, tuple[str, ...]] = {
    "BadgeVariant": ("Neutral", "Brand", "Success", "Warning", "Critical", "Info"),
    "BoxRole": ("Dashboard", "Card", "Group", "Separator"),
    "ButtonVariant": ("Primary", "Secondary", "Tertiary", "Destructive"),
    "CaptureSource": ("Camera", "Microphone"),
    "ChannelDirection": ("OutOnly", "TwoWay"),
    "ChartDataLabels": ("Off", "Ends"),
    "ChartKind": ("Line", "Bar", "Area", "Pie", "Scatter", "Heatmap"),
    "ChartLegendPosition": ("Top", "Right", "Bottom", "None"),
    "ChartXScale": ("Category", "Temporal"),
    "CompareOp": ("eq", "neq", "lt", "lte", "gt", "gte"),
    "DateStyle": ("Short", "Medium", "Long", "Full"),
    "DateTimeVariant": ("Date", "Time", "DateTime"),
    "DeterminismSource": ("Deterministic", "Clock", "Random", "Network"),
    "DurationStyle": ("Compact", "Clock", "Long"),
    "DurationUnit": ("Seconds", "Minutes", "Hours"),
    "EmbedPermission": ("AllowScripts", "AllowSameOrigin", "AllowForms", "AllowFullscreen"),
    "Emphasis": ("Quiet", "Normal", "Loud"),
    "FileReadEncoding": ("Text", "Base64", "DataUrl"),
    "FontVoice": ("Default", "Display", "Structural"),
    "HashStrictness": ("StrictReplay", "AdvisoryWarning", "Enforced"),
    "HeadingVariant": ("Standard", "Eyebrow", "Caption", "Lead"),
    "HostEffect": ("Pure", "ReadsHost", "WritesHost"),
    "IconSize": ("Small", "Medium", "Large"),
    "ImageAspect": ("Natural", "Square", "FourThree", "ThreeTwo", "SixteenNine"),
    "ImageFit": ("Natural", "Cover", "Contain"),
    "ImageLoading": ("Eager", "Lazy"),
    "ImageVariant": ("Default", "Avatar", "Rounded"),
    "LinkProtection": ("email",),
    "LiveRegionKind": ("polite", "assertive", "off"),
    "MathDisplay": ("Inline", "Block"),
    "ModalityKind": ("Modal", "Popover"),
    "Motion": (
        "None",
        "PulseDuringLoad",
        "FadeInOnMount",
        "SlideInFromBelow",
        "ShakeOnError",
        "RotateOnRefresh",
        "SlideInFromRight",
        "ExpandCollapse",
        "CrossFade",
        "SlideBetween",
    ),
    "NavigateTarget": ("Self", "Blank"),
    "Orientation": ("Vertical", "Horizontal"),
    "RelativeTimeUnit": ("Second", "Minute", "Hour", "Day", "Week", "Month", "Year"),
    "ScrollOrientation": ("Vertical", "Horizontal", "Both"),
    "SortDirection": ("asc", "desc"),
    "StyleRole": ("None", "Eyebrow", "Data", "Lede", "Caption"),
    "StyleWeight": ("Compact", "Standard", "Spacious"),
    "TextAnchor": ("Start", "Middle", "End"),
    "TextDirection": ("auto", "ltr", "rtl"),
    "TextFormat": ("email", "url", "tel"),
    "TimeGrain": ("Second", "Minute", "Hour", "Day"),
    "TimeStyle": ("Short", "Medium", "Long", "Full"),
    "ToneVariant": ("Default", "Subdued", "Brand", "Success", "Warning", "Critical", "Info"),
    "TrackKind": ("Subtitles", "Captions", "Descriptions", "Chapters"),
    "TrendPolarity": ("HigherIsBetter", "LowerIsBetter"),
}

#: The host-language spellings of the enums whose wire tokens are not PascalCase.
ENUM_HOST_CASES: dict[str, tuple[str, ...]] = {
    "CompareOp": ("Eq", "Neq", "Lt", "Lte", "Gt", "Gte"),
    "LinkProtection": ("Email",),
    "LiveRegionKind": ("Polite", "Assertive", "Off"),
    "SortDirection": ("Asc", "Desc"),
    "TextDirection": ("Auto", "Ltr", "Rtl"),
    "TextFormat": ("Email", "Url", "Tel"),
}

ENUM_SETS: dict[str, frozenset[str]] = {name: frozenset(cases) for name, cases in ENUM_CASES.items()}


# ── Node kinds ────────────────────────────────────────────────────────────

NodeKindTag = Literal[
    "Badge",
    "Box",
    "Button",
    "Callout",
    "Chart",
    "CodeBlock",
    "Custom",
    "DataGrid",
    "Disclosure",
    "Drawing",
    "Embed",
    "ErrorBoundary",
    "Fact",
    "FileUpload",
    "Filters",
    "Form",
    "FragmentDecl",
    "FragmentRef",
    "Heading",
    "Icon",
    "Image",
    "LabelValueRow",
    "Link",
    "List",
    "Map",
    "Markdown",
    "Math",
    "Media",
    "Metric",
    "Modal",
    "Mount",
    "Progress",
    "ScrollArea",
    "Select",
    "Skeleton",
    "Sparkline",
    "SplitPanel",
    "Stepper",
    "SummaryList",
    "Switch",
    "Tabs",
    "Toast",
    "Tree",
]

#: Every node-kind discriminator, sorted.
KINDS: tuple[str, ...] = (
    "Badge",
    "Box",
    "Button",
    "Callout",
    "Chart",
    "CodeBlock",
    "Custom",
    "DataGrid",
    "Disclosure",
    "Drawing",
    "Embed",
    "ErrorBoundary",
    "Fact",
    "FileUpload",
    "Filters",
    "Form",
    "FragmentDecl",
    "FragmentRef",
    "Heading",
    "Icon",
    "Image",
    "LabelValueRow",
    "Link",
    "List",
    "Map",
    "Markdown",
    "Math",
    "Media",
    "Metric",
    "Modal",
    "Mount",
    "Progress",
    "ScrollArea",
    "Select",
    "Skeleton",
    "Sparkline",
    "SplitPanel",
    "Stepper",
    "SummaryList",
    "Switch",
    "Tabs",
    "Toast",
    "Tree",
)

KIND_CATEGORY: dict[str, str] = {
    "Badge": "Display",
    "Box": "Layout",
    "Button": "Input",
    "Callout": "Display",
    "Chart": "Visualisation",
    "CodeBlock": "Display",
    "Custom": "Meta",
    "DataGrid": "Visualisation",
    "Disclosure": "Layout",
    "Drawing": "Display",
    "Embed": "Display",
    "ErrorBoundary": "Meta",
    "Fact": "Display",
    "FileUpload": "Input",
    "Filters": "Input",
    "Form": "Input",
    "FragmentDecl": "Meta",
    "FragmentRef": "Meta",
    "Heading": "Display",
    "Icon": "Display",
    "Image": "Display",
    "LabelValueRow": "Display",
    "Link": "Display",
    "List": "Display",
    "Map": "Visualisation",
    "Markdown": "Display",
    "Math": "Display",
    "Media": "Display",
    "Metric": "Display",
    "Modal": "Layout",
    "Mount": "Meta",
    "Progress": "Display",
    "ScrollArea": "Layout",
    "Select": "Input",
    "Skeleton": "Display",
    "Sparkline": "Display",
    "SplitPanel": "Layout",
    "Stepper": "Layout",
    "SummaryList": "Layout",
    "Switch": "Meta",
    "Tabs": "Layout",
    "Toast": "Display",
    "Tree": "Display",
}

#: Each kind's members in declaration order.
KIND_FIELDS: dict[str, tuple[F, ...]] = {
    "Badge": (
        F("label", "required", T_union_TextSource),
        F("variant", "omitDefault", T_enum_BadgeVariant, default="Neutral"),
    ),
    "Box": (
        F("children", "required", T_list_node),
        F("heading", "optional", T_union_TextSource),
        F("layout", "required", T_union_BoxLayout),
        F("role", "required", T_enum_BoxRole),
        F("keepTogether", "omitDefault", T_bool, default=False),
        F("breakBefore", "omitDefault", T_bool, default=False),
    ),
    "Button": (
        F("label", "required", T_union_TextSource),
        F("onClick", "required", T_union_Action),
        F("variant", "omitDefault", T_enum_ButtonVariant, default="Secondary"),
        F("icon", "optional", T_str),
        F("tooltip", "hostOnly", T_fn),
        F("disabled", "optional", T_union_Binding_bool),
    ),
    "Callout": (
        F("body", "required", T_union_TextSource),
        F("dismissable", "omitDefault", T_bool, default=False),
        F("tone", "omitDefault", T_enum_ToneVariant, default="Default"),
        F("heading", "optional", T_union_TextSource),
        F("icon", "optional", T_str),
    ),
    "Chart": (
        F("kind", "required", T_enum_ChartKind),
        F("source", "required", T_union_Binding_hosted_json),
        F("stacked", "omitDefault", T_bool, default=False),
        F("xField", "required", T_str),
        F("yFields", "required", T_list_str),
        F("title", "optional", T_union_TextSource),
        F("valueFormat", "optional", T_union_Format),
        F("xTitle", "optional", T_union_TextSource),
        F("yTitle", "optional", T_union_TextSource),
        F("subtitle", "optional", T_union_TextSource),
        F("legendPosition", "optional", T_enum_ChartLegendPosition),
        F("dataLabels", "optional", T_enum_ChartDataLabels),
        F("xScale", "optional", T_enum_ChartXScale),
        F("annotations", "optional", T_list_union_ChartAnnotation),
        F("onPointClick", "optional", T_fn),
    ),
    "CodeBlock": (
        F("code", "required", T_str),
        F("copyable", "omitDefault", T_bool, default=True),
        F("highlightLines", "omitDefault", T_list_int, default=[]),
        F("language", "required", T_str),
        F("lineNumbers", "omitDefault", T_bool, default=False),
    ),
    "Custom": (
        F("moduleId", "required", T_str),
        F("componentId", "required", T_str),
        F("props", "required", T_map_json),
        F("contentHash", "optional", T_record_ContentHash),
        F("exposedNodeIds", "optional", T_list_str),
    ),
    "DataGrid": (
        F("columns", "required", T_list_record_ColumnErased),
        F("editable", "omitDefault", T_bool, default=False),
        F("rowKey", "optional", T_fn),
        F("rowKeyField", "optional", T_str),
        F("sortStateKey", "optional", T_str),
        F("pageSize", "optional", T_int),
        F("pageStateKey", "optional", T_str),
        F("defaultSort", "optional", T_record_DefaultSort),
        F("editStateKey", "optional", T_str),
        F("reorderable", "omitDefault", T_bool, default=False),
        F("transferInKey", "optional", T_str),
        F("transferOutKey", "optional", T_str),
        F("keepRowsTogether", "omitDefault", T_bool, default=False),
        F("repeatHeader", "omitDefault", T_bool, default=False),
        F("exportable", "omitDefault", T_bool, default=False),
        F("windowStateKey", "optional", T_str),
        F("rowTotal", "optional", T_union_Binding_int),
        F("source", "required", T_union_Binding_hosted_json),
        F("staticRows", "optional", T_record_StaticRows),
        F("onRowClick", "optional", T_fn),
    ),
    "Disclosure": (
        F("children", "required", T_list_node),
        F("defaultOpen", "required", T_bool),
        F("heading", "required", T_union_TextSource),
        F("onToggle", "optional", T_fn),
        F("open", "required", T_union_Binding_bool),
    ),
    "Drawing": (
        F("description", "optional", T_union_TextSource),
        F("shapes", "required", T_list_union_Shape),
        F("style", "required", T_record_DrawStyle),
        F("title", "optional", T_union_TextSource),
        F("viewBox", "required", T_record_ViewBox),
    ),
    "Embed": (
        F("aspectRatio", "omitDefault", T_enum_ImageAspect, default="Natural"),
        F("permissions", "omitDefault", T_list_enum_EmbedPermission, default=[]),
        F("src", "required", T_union_Binding_str),
        F("title", "required", T_union_TextSource),
    ),
    "ErrorBoundary": (
        F("child", "required", T_node),
        F("fallback", "required", T_node),
    ),
    "Fact": (
        F("emphasis", "omitDefault", T_bool, default=False),
        F("help", "optional", T_union_TextSource),
        F("icon", "optional", T_str),
        F("label", "required", T_union_TextSource),
        F("tone", "omitDefault", T_enum_ToneVariant, default="Default"),
        F("value", "required", T_union_TextSource),
    ),
    "FileUpload": (
        F("accept", "required", T_list_str),
        F("label", "required", T_union_TextSource),
        F("multiple", "omitDefault", T_bool, default=False),
        F("onSelect", "optional", T_fn),
        F("disabled", "optional", T_union_Binding_bool),
        F("acceptPaste", "omitDefault", T_bool, default=False),
        F("dropTarget", "omitDefault", T_bool, default=False),
        F("capture", "optional", T_enum_CaptureSource),
        F("destination", "optional", T_str),
        F("maxBytes", "optional", T_int),
        F("maxFiles", "optional", T_int),
    ),
    "Filters": (F("items", "required", T_list_record_FilterSpec),),
    "Form": (
        F("fields", "required", T_list_record_FormField),
        F("onSubmit", "required", T_union_Action),
        F("submitLabel", "required", T_union_TextSource),
        F("disabled", "optional", T_union_Binding_bool),
    ),
    "FragmentDecl": (
        F("body", "required", T_node),
        F("name", "required", T_str),
        F("holes", "omitDefault", T_list_union_HoleDecl, default=[]),
        F(
            "effect",
            "omitDefault",
            T_record_EffectClass,
            default={"determinism": "Deterministic", "hostEffect": "Pure"},
        ),
    ),
    "FragmentRef": (
        F("name", "required", T_str),
        F("args", "optional", T_map_union_FragmentArg),
    ),
    "Heading": (
        F("level", "required", T_int),
        F("text", "required", T_union_TextSource),
        F("variant", "omitDefault", T_enum_HeadingVariant, default="Standard"),
    ),
    "Icon": (
        F("icon", "required", T_str),
        F("size", "omitDefault", T_enum_IconSize, default="Medium"),
        F("tone", "omitDefault", T_enum_ToneVariant, default="Default"),
        F("label", "optional", T_str),
    ),
    "Image": (
        F("alt", "required", T_union_TextSource),
        F("src", "required", T_union_Binding_str),
        F("variant", "omitDefault", T_enum_ImageVariant, default="Default"),
        F("fit", "omitDefault", T_enum_ImageFit, default="Natural"),
        F("aspectRatio", "omitDefault", T_enum_ImageAspect, default="Natural"),
        F("loading", "omitDefault", T_enum_ImageLoading, default="Eager"),
        F("srcSet", "omitDefault", T_list_record_SrcSetEntry, default=[]),
        F("expandable", "omitDefault", T_bool, default=False),
        F("caption", "optional", T_union_TextSource),
    ),
    "LabelValueRow": (
        F("emphasis", "omitDefault", T_bool, default=False),
        F("format", "omitDefault", T_union_CellFormat, default={"$type": "None"}),
        F("label", "required", T_union_TextSource),
        F("value", "required", T_union_Binding_float),
        F("help", "optional", T_union_TextSource),
    ),
    "Link": (
        F("href", "required", T_union_Binding_str),
        F("label", "required", T_union_TextSource),
        F("download", "omitDefault", T_bool, default=False),
        F("rel", "optional", T_str),
        F("target", "optional", T_str),
        F("protection", "optional", T_enum_LinkProtection),
    ),
    "List": (
        F("items", "required", T_list_union_TextSource),
        F("ordered", "omitDefault", T_bool, default=False),
    ),
    "Map": (
        F("centreLatitude", "required", T_float),
        F("centreLongitude", "required", T_float),
        F("source", "required", T_union_Binding_list_record_MapMarker),
        F("zoom", "required", T_int),
        F("onMarkerClick", "optional", T_fn),
    ),
    "Markdown": (F("text", "required", T_union_TextSource),),
    "Math": (
        F("source", "required", T_str),
        F("display", "required", T_enum_MathDisplay),
    ),
    "Media": (
        F("controls", "omitDefault", T_bool, default=True),
        F("kind", "required", T_union_MediaKind),
        F("label", "required", T_union_TextSource),
        F("loop", "omitDefault", T_bool, default=False),
        F("src", "required", T_union_Binding_str),
        F("tracks", "omitDefault", T_list_record_TrackEntry, default=[]),
        F("transcript", "optional", T_union_TextSource),
    ),
    "Metric": (
        F("label", "required", T_union_TextSource),
        F("value", "required", T_union_Binding_float),
        F("format", "omitDefault", T_union_CellFormat, default={"$type": "None"}),
        F("tone", "omitDefault", T_enum_ToneVariant, default="Default"),
        F("weight", "omitDefault", T_enum_StyleWeight, default="Standard"),
        F("emphasis", "omitDefault", T_enum_Emphasis, default="Normal"),
        F("trend", "optional", T_union_Binding_float),
        F("trendFormat", "optional", T_union_CellFormat),
        F("trendPolarity", "omitDefault", T_enum_TrendPolarity, default="HigherIsBetter"),
        F("comparisons", "omitDefault", T_list_record_MetricComparison, default=[]),
        F("icon", "optional", T_str),
        F("subtext", "optional", T_union_TextSource),
    ),
    "Modal": (
        F("children", "required", T_list_node),
        F("dismissable", "omitDefault", T_bool, default=True),
        F("onDismiss", "optional", T_union_Action),
        F("open", "required", T_union_Binding_bool),
        F("heading", "optional", T_union_TextSource),
        F("modality", "omitDefault", T_enum_ModalityKind, default="Modal"),
        F("anchor", "optional", T_str),
    ),
    "Mount": (
        F("capabilities", "required", T_list_str),
        F("channel", "required", T_record_GuestChannel),
        F("inputs", "optional", T_map_union_FragmentArg),
        F("onBubble", "optional", T_fn),
        F("scopeId", "required", T_str),
    ),
    "Progress": (
        F("fraction", "required", T_union_Binding_float),
        F("indeterminate", "omitDefault", T_bool, default=False),
        F("tone", "omitDefault", T_enum_ToneVariant, default="Default"),
        F("label", "optional", T_union_TextSource),
        F("caveat", "optional", T_union_TextSource),
    ),
    "ScrollArea": (
        F("children", "required", T_list_node),
        F("orientation", "required", T_enum_ScrollOrientation),
        F("maxHeight", "optional", T_int),
        F("maxWidth", "optional", T_int),
    ),
    "Select": (
        F("label", "required", T_union_TextSource),
        F("onChange", "optional", T_fn),
        F("onChangeMulti", "optional", T_fn),
        F("source", "required", T_union_Binding_list_record_SelectOption),
        F("value", "optional", T_union_Binding_str),
        F("placeholder", "optional", T_union_TextSource),
        F("disabled", "optional", T_union_Binding_bool),
        F("multiple", "optional", T_bool),
        F("values", "optional", T_union_Binding_list_str),
    ),
    "Skeleton": (F("rows", "required", T_int),),
    "Sparkline": (F("source", "required", T_union_Binding_list_float),),
    "SplitPanel": (
        F("children", "required", T_list_node),
        F("weight", "required", T_float),
    ),
    "Stepper": (
        F("activeStep", "omitDefault", T_union_Binding_int, default={"$type": "Static", "value": 0}),
        F("children", "required", T_list_node),
        F("onSelect", "optional", T_fn),
    ),
    "SummaryList": (
        F("children", "required", T_list_node),
        F("heading", "optional", T_union_TextSource),
    ),
    "Switch": (
        F("autoAdvanceMs", "optional", T_int),
        F("cases", "required", T_list_record_SwitchCase),
        F("default", "required", T_node),
        F("on", "optional", T_union_Binding_str),
        F("stateKey", "optional", T_str),
    ),
    "Tabs": (
        F("activeIndex", "omitDefault", T_union_Binding_int, default={"$type": "Static", "value": 0}),
        F("children", "required", T_list_node),
        F("orientation", "omitDefault", T_enum_Orientation, default="Horizontal"),
        F("onSelect", "optional", T_fn),
        F("onSelectTag", "optional", T_fn),
        F("tabHeaders", "optional", T_list_record_TabHeader),
        F("tabTags", "optional", T_list_str),
        F("activeTag", "optional", T_union_Binding_str),
    ),
    "Toast": (
        F("dismissable", "omitDefault", T_bool, default=True),
        F("message", "required", T_union_TextSource),
        F("open", "required", T_union_Binding_bool),
        F("tone", "omitDefault", T_enum_ToneVariant, default="Default"),
    ),
    "Tree": (
        F("expandedStateKey", "optional", T_str),
        F("items", "required", T_list_record_TreeItem),
        F("onSelect", "optional", T_fn),
        F("selectionStateKey", "optional", T_str),
    ),
}


# ── The node envelope ─────────────────────────────────────────────────────

NODE_FIELDS: tuple[F, ...] = (
    F("accessibility", "optional", T_record_Accessibility),
    F("extraAttributes", "hostOnly", T_fn),
    F("fallback", "optional", T_node),
    F("motion", "hostOnly", T_fn),
    F("state", "optional", T_record_StateBehaviour),
    F("style", "optional", T_record_SemanticStyle),
    F("tooltip", "optional", T_union_TextSource),
    F("visible", "optional", T_union_Binding_bool),
)


# ── Records ───────────────────────────────────────────────────────────────

RECORD_FIELDS: dict[str, tuple[F, ...]] = {
    "Accessibility": (
        F("describedBy", "optional", T_str),
        F("hidden", "optional", T_union_Binding_bool),
        F("label", "optional", T_union_Binding_str),
        F("labelledBy", "optional", T_str),
        F("liveRegion", "optional", T_enum_LiveRegionKind),
        F("role", "optional", T_hosted_json),
        F("speak", "optional", T_union_TextSource),
    ),
    "ButtonGroupItem": (
        F("label", "required", T_union_TextSource),
        F("onClick", "optional", T_fn),
    ),
    "ColumnErased": (
        F("field", "optional", T_str),
        F("sortable", "optional", T_bool),
        F("editable", "optional", T_bool),
        F("format", "omitDefault", T_union_CellFormat, default={"$type": "None"}),
        F("kind", "required", T_union_CellKindErased),
        F("label", "required", T_str),
        F("value", "optional", T_fn),
        F("width", "omitDefault", T_union_ColumnWidth, default={"$type": "Auto"}),
    ),
    "CompareRule": (
        F("against", "required", T_union_Binding_json),
        F("op", "required", T_enum_CompareOp),
    ),
    "ContentHash": (
        F("algorithm", "required", T_str),
        F("hash", "required", T_str),
        F("strictness", "required", T_enum_HashStrictness),
    ),
    "DateTimeRangePair": (
        F("from", "required", T_str),
        F("to", "required", T_str),
    ),
    "DefaultSort": (
        F("column", "required", T_int),
        F("direction", "required", T_enum_SortDirection),
    ),
    "DrawPoint": (
        F("x", "required", T_float),
        F("y", "required", T_float),
    ),
    "DrawStyle": (
        F("emphasis", "optional", T_enum_Emphasis),
        F("fill", "optional", T_union_Binding_str),
        F("fontFamily", "optional", T_str),
        F("fontSize", "optional", T_float),
        F("markId", "optional", T_str),
        F("opacity", "optional", T_union_Binding_float),
        F("rotation", "optional", T_float),
        F("stroke", "optional", T_union_Binding_str),
        F("strokeWidth", "optional", T_union_Binding_float),
        F("textAnchor", "optional", T_enum_TextAnchor),
        F("tip", "optional", T_union_TextSource),
    ),
    "EffectClass": (
        F("determinism", "required", T_enum_DeterminismSource),
        F("hostEffect", "required", T_enum_HostEffect),
    ),
    "FieldRule": (
        F("compare", "optional", T_record_CompareRule),
        F("format", "optional", T_enum_TextFormat),
        F("maxLength", "optional", T_int),
        F("message", "optional", T_union_TextSource),
        F("minLength", "optional", T_int),
        F("pattern", "optional", T_str),
    ),
    "FilterSpec": (
        F("kind", "required", T_union_FormFieldKind),
        F("label", "required", T_union_TextSource),
        F("name", "required", T_str),
    ),
    "FormField": (
        F("id", "required", T_str),
        F("kind", "required", T_union_FormFieldKind),
        F("label", "required", T_union_TextSource),
        F("required", "omitDefault", T_bool, default=False),
        F("help", "optional", T_union_TextSource),
        F("rule", "optional", T_record_FieldRule),
    ),
    "GuestChannel": (
        F("direction", "required", T_enum_ChannelDirection),
        F("messageShape", "optional", T_str),
    ),
    "InvokeArg": (
        F("addr", "required", T_str),
        F("value", "required", T_str),
    ),
    "MapMarker": (
        F("label", "required", T_str),
        F("latitude", "required", T_float),
        F("longitude", "required", T_float),
    ),
    "MetricComparison": (
        F("label", "required", T_union_TextSource),
        F("value", "required", T_union_Binding_float),
        F("format", "omitDefault", T_union_CellFormat, default={"$type": "None"}),
        F("polarity", "omitDefault", T_enum_TrendPolarity, default="HigherIsBetter"),
    ),
    "RangePair": (
        F("max", "required", T_float),
        F("min", "required", T_float),
    ),
    "SelectOption": (
        F("label", "required", T_str),
        F("value", "required", T_str),
    ),
    "SemanticStyle": (
        F("direction", "omitDefault", T_enum_TextDirection, default="auto"),
        F("emphasis", "omitDefault", T_enum_Emphasis, default="Normal"),
        F("role", "omitDefault", T_enum_StyleRole, default="None"),
        F("tone", "omitDefault", T_enum_ToneVariant, default="Default"),
        F("voice", "omitDefault", T_enum_FontVoice, default="Default"),
        F("weight", "omitDefault", T_enum_StyleWeight, default="Standard"),
    ),
    "SrcSetEntry": (
        F("src", "required", T_union_Binding_str),
        F("width", "required", T_int),
    ),
    "StateBehaviour": (
        F("onEmpty", "optional", T_node),
        F("onError", "optional", T_fn),
        F("onLoading", "optional", T_node),
    ),
    "StaticRows": (
        F("defaultSort", "optional", T_record_DefaultSort),
        F("headers", "required", T_list_union_TextSource),
        F("rows", "required", T_list_list_union_TextSource),
        F("sortable", "optional", T_bool),
    ),
    "StyleDelta": (
        F("direction", "optional", T_enum_TextDirection),
        F("emphasis", "optional", T_enum_Emphasis),
        F("role", "optional", T_enum_StyleRole),
        F("tone", "optional", T_enum_ToneVariant),
        F("voice", "optional", T_enum_FontVoice),
        F("weight", "optional", T_enum_StyleWeight),
    ),
    "SwitchCase": (
        F("child", "required", T_node),
        F("match", "optional", T_str),
        F("when", "optional", T_union_Binding_bool),
    ),
    "TabHeader": (
        F("label", "required", T_union_TextSource),
        F("icon", "optional", T_str),
        F("disabled", "optional", T_union_Binding_bool),
    ),
    "TrackEntry": (
        F("default", "omitDefault", T_bool, default=False),
        F("kind", "required", T_enum_TrackKind),
        F("label", "required", T_union_TextSource),
        F("src", "required", T_union_Binding_str),
        F("srcLang", "required", T_str),
    ),
    "TransformParam": (
        F("from", "required", T_union_Binding_json),
        F("name", "required", T_str),
    ),
    "TreeItem": (
        F("children", "omitDefault", T_list_record_TreeItem, default=[]),
        F("icon", "optional", T_str),
        F("id", "required", T_str),
        F("label", "required", T_union_TextSource),
    ),
    "ViewBox": (
        F("height", "required", T_float),
        F("minX", "required", T_float),
        F("minY", "required", T_float),
        F("width", "required", T_float),
    ),
}


# ── Unions ────────────────────────────────────────────────────────────────

#: Every union's case discriminators, in declaration order.
UNION_CASES: dict[str, tuple[str, ...]] = {
    "Action": (
        "Chain",
        "WriteToClipboard",
        "Dispatch",
        "Invoke",
        "ReadFileBody",
        "Call",
        "Navigate",
        "CommitLocal",
        "Notify",
        "SetState",
        "AiTool",
        "Print",
        "Confirm",
        "Focus",
    ),
    "Binding": (
        "Static",
        "Query",
        "Filter",
        "Selection",
        "State",
        "Now",
        "Computed",
        "Local",
        "Format",
        "I18n",
        "Transform",
        "Expr",
        "Invoke",
    ),
    "BoxLayout": ("Auto", "Flex", "Grid", "Masonry"),
    "CallResultTarget": ("State", "Query"),
    "CellFormat": (
        "None",
        "Number",
        "Currency",
        "Percent",
        "SignificantDigits",
        "DateTime",
        "Duration",
        "RelativeTime",
        "Custom",
    ),
    "CellKindErased": (
        "Text",
        "Numeric",
        "Date",
        "Editable",
        "Checkbox",
        "Button",
        "ButtonGroup",
        "Link",
        "Pill",
        "TonedPill",
        "Progress",
        "Custom",
    ),
    "ChartAnnotation": ("ReferenceLine", "EventMarker", "RangeBand"),
    "ChartAnnotationRange": ("ValueRange", "XRange"),
    "ChartAnnotationX": ("Category", "Date"),
    "ColumnWidth": ("Auto", "Fixed", "Flex"),
    "CurveCommand": ("MoveTo", "LineTo", "CubicTo", "QuadraticTo", "Close"),
    "FormFieldKind": (
        "Text",
        "Number",
        "Checkbox",
        "Toggle",
        "Choice",
        "TextArea",
        "RangedNumber",
        "Range",
        "SegmentedChoice",
        "DateTime",
        "DateTimeRange",
        "Combobox",
        "Rating",
        "Color",
        "Tokens",
    ),
    "Format": ("Number", "Currency", "Percent", "DateTime", "RelativeTime", "Duration", "Since"),
    "FragmentArg": ("Int", "Float", "Bool", "Str", "SlotArg"),
    "HoleDecl": ("Value", "Slot", "Repeat"),
    "HoleValueSpace": ("IntRange", "FloatRange", "StringLen", "Enum", "AnyString"),
    "LocalFlushTrigger": ("OnBlur", "OnSubmit", "OnDebounce", "OnCommitAction"),
    "LocaleSource": ("Ambient", "Explicit"),
    "MediaKind": ("Video", "Audio"),
    "Scalar": ("Int", "Float", "Bool", "Str"),
    "Shape": ("Group", "Rectangle", "Line", "Polyline", "Polygon", "Curve", "Circle", "Ellipse", "Label"),
    "TextSource": ("Literal", "Bound", "I18n"),
}

#: The case whose bare-value shorthand is the canonical form (WIRE_FORMAT §16).
UNION_TRANSPARENT_CASE: dict[str, str] = {
    "TextSource": "Literal",
}

#: Each union case's members, in declaration order.
UNION_CASE_FIELDS: dict[str, dict[str, tuple[F, ...]]] = {
    "Action": {
        "Chain": (F("ops", "required", T_list_union_Action),),
        "WriteToClipboard": (F("text", "required", T_union_TextSource),),
        "Dispatch": (F("msg", "hostOnly", T_fn),),
        "Invoke": (
            F("capabilityId", "required", T_str),
            F("args", "required", T_list_record_InvokeArg),
        ),
        "ReadFileBody": (
            F("fileRef", "required", T_str),
            F("fileHandle", "hostOnly", T_fn),
            F("encoding", "required", T_enum_FileReadEncoding),
            F("onRead", "optional", T_fn),
        ),
        "Call": (
            F("endpoint", "required", T_str),
            F("onResult", "optional", T_fn),
            F("into", "optional", T_union_CallResultTarget),
        ),
        "Navigate": (
            F("route", "required", T_union_TextSource),
            F("target", "omitDefault", T_enum_NavigateTarget, default="Self"),
        ),
        "CommitLocal": (F("nodeId", "required", T_str),),
        "Notify": (
            F("channel", "required", T_str),
            F("payload", "required", T_json),
        ),
        "SetState": (
            F("key", "required", T_str),
            F("value", "optional", T_json),
            F("valueFrom", "optional", T_union_Binding_json),
        ),
        "AiTool": (
            F("toolName", "required", T_str),
            F("args", "required", T_json),
        ),
        "Print": (),
        "Confirm": (
            F("prompt", "required", T_union_TextSource),
            F("onConfirm", "required", T_union_Action),
            F("onCancel", "optional", T_union_Action),
        ),
        "Focus": (F("nodeId", "required", T_str),),
    },
    "Binding": {
        "Static": (F("value", "optional", T_var_T),),
        "Query": (
            F("name", "required", T_str),
            F("accessor", "hostOnly", T_fn),
            F("dependsOn", "optional", T_list_str),
        ),
        "Filter": (
            F("name", "required", T_str),
            F("defaultValue", "optional", T_var_T),
        ),
        "Selection": (
            F("nodeId", "required", T_str),
            F("accessor", "hostOnly", T_fn),
            F("defaultValue", "optional", T_var_T),
            F("field", "optional", T_str),
        ),
        "State": (
            F("key", "required", T_str),
            F("defaultValue", "optional", T_var_T),
        ),
        "Now": (
            F("accessor", "hostOnly", T_fn),
            F("grain", "optional", T_enum_TimeGrain),
        ),
        "Computed": (F("fn", "required", T_fn),),
        "Local": (
            F("flushOn", "required", T_union_LocalFlushTrigger),
            F("format", "required", T_fn),
            F("initialFrom", "required", T_union_Binding_var_T),
            F("onCommit", "optional", T_fn),
            F("parse", "required", T_fn),
            F("codec", "optional", T_union_Format),
            F("commitTo", "optional", T_str),
        ),
        "Format": (
            F("source", "required", T_union_Binding_float),
            F("format", "required", T_union_Format),
            F("locale", "required", T_union_LocaleSource),
        ),
        "I18n": (
            F("key", "required", T_str),
            F("args", "optional", T_map_union_Binding_json),
        ),
        "Transform": (
            F("source", "required", T_hosted_json),
            F("pipeline", "required", T_list_hosted_json),
            F("params", "optional", T_list_record_TransformParam),
        ),
        "Expr": (
            F("expr", "required", T_hosted_json),
            F("params", "optional", T_list_record_TransformParam),
        ),
        "Invoke": (
            F("capabilityId", "required", T_str),
            F("args", "required", T_list_record_InvokeArg),
        ),
    },
    "BoxLayout": {
        "Auto": (),
        "Flex": (
            F("direction", "required", T_enum_Orientation),
            F("wrap", "omitDefault", T_bool, default=False),
            F("gap", "optional", T_int),
        ),
        "Grid": (
            F("cols", "required", T_int),
            F("templateColumns", "optional", T_str),
            F("gap", "optional", T_int),
        ),
        "Masonry": (
            F("cols", "required", T_int),
            F("gap", "optional", T_int),
        ),
    },
    "CallResultTarget": {
        "State": (F("key", "required", T_str),),
        "Query": (F("name", "required", T_str),),
    },
    "CellFormat": {
        "None": (),
        "Number": (F("decimals", "optional", T_int),),
        "Currency": (F("code", "required", T_str),),
        "Percent": (F("decimals", "optional", T_int),),
        "SignificantDigits": (F("digits", "required", T_int),),
        "DateTime": (F("format", "required", T_str),),
        "Duration": (
            F("unit", "required", T_enum_DurationUnit),
            F("style", "required", T_enum_DurationStyle),
        ),
        "RelativeTime": (F("unit", "required", T_enum_RelativeTimeUnit),),
        "Custom": (F("fn", "required", T_fn),),
    },
    "CellKindErased": {
        "Text": (),
        "Numeric": (),
        "Date": (),
        "Editable": (F("onEdit", "optional", T_fn),),
        "Checkbox": (
            F("get", "required", T_fn),
            F("onToggle", "optional", T_fn),
        ),
        "Button": (
            F("label", "required", T_union_TextSource),
            F("onClick", "optional", T_fn),
        ),
        "ButtonGroup": (F("buttons", "required", T_list_record_ButtonGroupItem),),
        "Link": (
            F("hrefFn", "required", T_fn),
            F("labelFn", "required", T_fn),
        ),
        "Pill": (
            F("labelFn", "required", T_fn),
            F("toneFn", "required", T_fn),
        ),
        "TonedPill": (
            F("field", "required", T_str),
            F("map", "required", T_map_enum_ToneVariant),
            F("default", "omitDefault", T_enum_ToneVariant, default="Default"),
        ),
        "Progress": (
            F("fractionFn", "required", T_fn),
            F("labelFn", "optional", T_fn),
        ),
        "Custom": (F("fn", "required", T_fn),),
    },
    "ChartAnnotation": {
        "ReferenceLine": (
            F("value", "required", T_float),
            F("label", "optional", T_union_TextSource),
        ),
        "EventMarker": (
            F("at", "required", T_union_ChartAnnotationX),
            F("label", "optional", T_union_TextSource),
        ),
        "RangeBand": (
            F("range", "required", T_union_ChartAnnotationRange),
            F("label", "optional", T_union_TextSource),
        ),
    },
    "ChartAnnotationRange": {
        "ValueRange": (
            F("from", "required", T_float),
            F("to", "required", T_float),
        ),
        "XRange": (
            F("from", "required", T_union_ChartAnnotationX),
            F("to", "required", T_union_ChartAnnotationX),
        ),
    },
    "ChartAnnotationX": {
        "Category": (F("key", "required", T_str),),
        "Date": (F("iso", "required", T_str),),
    },
    "ColumnWidth": {
        "Auto": (),
        "Fixed": (F("pixels", "required", T_int),),
        "Flex": (F("weight", "required", T_float),),
    },
    "CurveCommand": {
        "MoveTo": (F("to", "required", T_record_DrawPoint),),
        "LineTo": (F("to", "required", T_record_DrawPoint),),
        "CubicTo": (
            F("control1", "required", T_record_DrawPoint),
            F("control2", "required", T_record_DrawPoint),
            F("to", "required", T_record_DrawPoint),
        ),
        "QuadraticTo": (
            F("control", "required", T_record_DrawPoint),
            F("to", "required", T_record_DrawPoint),
        ),
        "Close": (),
    },
    "FormFieldKind": {
        "Text": (
            F("value", "optional", T_hosted_as_union_Binding_str),
            F("onChange", "optional", T_fn),
        ),
        "Number": (
            F("value", "optional", T_hosted_as_union_Binding_float),
            F("onChange", "optional", T_fn),
        ),
        "Checkbox": (
            F("value", "optional", T_hosted_as_union_Binding_bool),
            F("onChange", "optional", T_fn),
        ),
        "Toggle": (
            F("value", "optional", T_hosted_as_union_Binding_bool),
            F("onChange", "optional", T_fn),
        ),
        "Choice": (
            F("options", "required", T_union_Binding_list_record_SelectOption),
            F("value", "optional", T_hosted_as_union_Binding_str),
            F("onChange", "optional", T_fn),
        ),
        "TextArea": (
            F("value", "optional", T_hosted_as_union_Binding_str),
            F("onChange", "optional", T_fn),
            F("rows", "required", T_int),
        ),
        "RangedNumber": (
            F("value", "optional", T_hosted_as_union_Binding_float),
            F("onChange", "optional", T_fn),
            F("min", "optional", T_float),
            F("max", "optional", T_float),
            F("step", "optional", T_float),
        ),
        "Range": (
            F("value", "optional", T_hosted_json),
            F("onChange", "optional", T_fn),
            F("min", "optional", T_float),
            F("max", "optional", T_float),
            F("step", "optional", T_float),
        ),
        "SegmentedChoice": (
            F("options", "required", T_union_Binding_list_record_SelectOption),
            F("value", "optional", T_hosted_as_union_Binding_str),
            F("onChange", "optional", T_fn),
            F("orientation", "omitDefault", T_enum_Orientation, default="Horizontal"),
        ),
        "DateTime": (
            F("value", "optional", T_hosted_as_union_Binding_str),
            F("onChange", "optional", T_fn),
            F("variant", "required", T_enum_DateTimeVariant),
            F("min", "optional", T_str),
            F("max", "optional", T_str),
            F("step", "optional", T_float),
        ),
        "DateTimeRange": (
            F("value", "optional", T_hosted_json),
            F("onChange", "optional", T_fn),
            F("variant", "required", T_enum_DateTimeVariant),
            F("min", "optional", T_str),
            F("max", "optional", T_str),
            F("step", "optional", T_float),
        ),
        "Combobox": (
            F("allowFreeText", "omitDefault", T_bool, default=False),
            F("onChange", "optional", T_fn),
            F("options", "required", T_union_Binding_list_record_SelectOption),
            F("value", "optional", T_hosted_as_union_Binding_str),
        ),
        "Rating": (
            F("allowHalf", "omitDefault", T_bool, default=False),
            F("max", "required", T_int),
            F("onChange", "optional", T_fn),
            F("value", "optional", T_hosted_as_union_Binding_float),
        ),
        "Color": (
            F("onChange", "optional", T_fn),
            F("value", "optional", T_hosted_as_union_Binding_str),
        ),
        "Tokens": (
            F("allowFreeText", "omitDefault", T_bool, default=True),
            F("onChange", "optional", T_fn),
            F("suggestions", "optional", T_union_Binding_list_record_SelectOption),
            F("value", "optional", T_hosted_as_union_Binding_list_str),
        ),
    },
    "Format": {
        "Number": (F("decimals", "optional", T_int),),
        "Currency": (F("isoCode", "required", T_str),),
        "Percent": (F("decimals", "optional", T_int),),
        "DateTime": (
            F("dateStyle", "optional", T_enum_DateStyle),
            F("timeStyle", "optional", T_enum_TimeStyle),
        ),
        "RelativeTime": (F("unit", "required", T_enum_RelativeTimeUnit),),
        "Duration": (
            F("unit", "required", T_enum_DurationUnit),
            F("style", "required", T_enum_DurationStyle),
        ),
        "Since": (F("unit", "optional", T_enum_RelativeTimeUnit),),
    },
    "FragmentArg": {
        "Int": (F("value", "required", T_int),),
        "Float": (F("value", "required", T_float),),
        "Bool": (F("value", "required", T_bool),),
        "Str": (F("value", "required", T_str),),
        "SlotArg": (F("tree", "required", T_node),),
    },
    "HoleDecl": {
        "Value": (
            F("name", "required", T_str),
            F("space", "required", T_union_HoleValueSpace),
            F("default", "optional", T_union_Scalar),
        ),
        "Slot": (
            F("name", "required", T_str),
            F("kindConstraint", "optional", T_str),
        ),
        "Repeat": (
            F("name", "required", T_str),
            F("countSpace", "required", T_union_HoleValueSpace),
        ),
    },
    "HoleValueSpace": {
        "IntRange": (
            F("min", "required", T_int),
            F("max", "required", T_int),
        ),
        "FloatRange": (
            F("min", "required", T_float),
            F("max", "required", T_float),
        ),
        "StringLen": (
            F("minLen", "required", T_int),
            F("maxLen", "required", T_int),
        ),
        "Enum": (F("choices", "required", T_list_str),),
        "AnyString": (),
    },
    "LocalFlushTrigger": {
        "OnBlur": (),
        "OnSubmit": (),
        "OnDebounce": (F("milliseconds", "required", T_int),),
        "OnCommitAction": (),
    },
    "LocaleSource": {
        "Ambient": (),
        "Explicit": (F("tag", "required", T_str),),
    },
    "MediaKind": {
        "Video": (
            F("autoplay", "omitDefault", T_bool, default=False),
            F("poster", "optional", T_union_Binding_str),
        ),
        "Audio": (),
    },
    "Scalar": {
        "Int": (F("value", "required", T_int),),
        "Float": (F("value", "required", T_float),),
        "Bool": (F("value", "required", T_bool),),
        "Str": (F("value", "required", T_str),),
    },
    "Shape": {
        "Group": (
            F("children", "required", T_list_union_Shape),
            F("style", "required", T_record_DrawStyle),
        ),
        "Rectangle": (
            F("x", "required", T_float),
            F("y", "required", T_float),
            F("width", "required", T_float),
            F("height", "required", T_float),
            F("cornerRadius", "optional", T_float),
            F("style", "required", T_record_DrawStyle),
        ),
        "Line": (
            F("x1", "required", T_float),
            F("y1", "required", T_float),
            F("x2", "required", T_float),
            F("y2", "required", T_float),
            F("style", "required", T_record_DrawStyle),
        ),
        "Polyline": (
            F("points", "required", T_list_record_DrawPoint),
            F("style", "required", T_record_DrawStyle),
        ),
        "Polygon": (
            F("points", "required", T_list_record_DrawPoint),
            F("style", "required", T_record_DrawStyle),
        ),
        "Curve": (
            F("commands", "required", T_list_union_CurveCommand),
            F("style", "required", T_record_DrawStyle),
        ),
        "Circle": (
            F("cx", "required", T_float),
            F("cy", "required", T_float),
            F("r", "required", T_float),
            F("style", "required", T_record_DrawStyle),
        ),
        "Ellipse": (
            F("cx", "required", T_float),
            F("cy", "required", T_float),
            F("rx", "required", T_float),
            F("ry", "required", T_float),
            F("style", "required", T_record_DrawStyle),
        ),
        "Label": (
            F("x", "required", T_float),
            F("y", "required", T_float),
            F("text", "required", T_union_TextSource),
            F("style", "required", T_record_DrawStyle),
        ),
    },
    "TextSource": {
        "Literal": (F("text", "required", T_str),),
        "Bound": (F("binding", "required", T_union_Binding_str),),
        "I18n": (
            F("key", "required", T_str),
            F("args", "required", T_map_hosted_json),
        ),
    },
}


# ── Tree ops ──────────────────────────────────────────────────────────────

OP_FIELDS: dict[str, tuple[F, ...]] = {
    "Batch": (F("ops", "required", T_list_op),),
    "EditNode": (
        F("newKind", "required", T_kind),
        F("target", "required", T_str),
    ),
    "InsertChild": (
        F("child", "required", T_node),
        F("parentId", "required", T_str),
    ),
    "MoveNode": (
        F("newParentId", "required", T_str),
        F("target", "required", T_str),
    ),
    "PatchStyle": (
        F("style", "required", T_record_StyleDelta),
        F("target", "required", T_str),
    ),
    "RemoveNode": (F("target", "required", T_str),),
    "ReorderChildren": (
        F("newOrder", "required", T_list_str),
        F("parentId", "required", T_str),
    ),
    "ReplaceBinding": (
        F("binding", "required", T_union_Binding_json),
        F("slot", "required", T_str),
        F("target", "required", T_str),
    ),
    "ReplaceRoot": (F("node", "required", T_node),),
    "UpdateProp": (
        F("path", "required", T_str),
        F("target", "required", T_str),
        F("value", "required", T_json),
    ),
    "UpdateState": (
        F("state", "required", T_record_StateBehaviour),
        F("target", "required", T_str),
    ),
    "UpdateStyle": (
        F("style", "required", T_record_SemanticStyle),
        F("target", "required", T_str),
    ),
}

#: The §21 hardening vocabulary: the gated kind, the placeholder and the literal-case names.
HARDEN: dict[str, object] = {
    "gatedKind": "Custom",
    "placeholderField": "text",
    "placeholderKind": "Markdown",
    "textLiteralCase": "Literal",
    "textLiteralField": "text",
    "transparentUnions": [{"case": "Literal", "union": "TextSource"}],
    "valueLiteralCase": "Static",
    "valueLiteralField": "value",
}


# ── Derived tables (projections of the rows above) ────────────────────────

KINDS_BY_CATEGORY: dict[str, frozenset[str]] = {
    "Display": frozenset(k for k, c in KIND_CATEGORY.items() if c == "Display"),
    "Input": frozenset(k for k, c in KIND_CATEGORY.items() if c == "Input"),
    "Layout": frozenset(k for k, c in KIND_CATEGORY.items() if c == "Layout"),
    "Meta": frozenset(k for k, c in KIND_CATEGORY.items() if c == "Meta"),
    "Visualisation": frozenset(k for k, c in KIND_CATEGORY.items() if c == "Visualisation"),
}

#: Each kind's REQUIRED wire members.
REQUIRED_FIELDS: dict[str, frozenset[str]] = {
    kind: frozenset(f.name for f in fields if f.required) for kind, fields in KIND_FIELDS.items()
}

#: Each kind's omit-at-default members and the wire value an omission means.
OMIT_DEFAULTS: dict[str, dict[str, object]] = {
    kind: {f.name: f.default for f in fields if f.optionality == "omitDefault"} for kind, fields in KIND_FIELDS.items()
}


def _is_child(t: T) -> bool:
    return t.kind == "node" or (t.kind == "list" and t.of is not None and t.of.kind == "node")


#: Each kind's child slots: the members that carry a node or a list of nodes.
CHILD_SLOTS: dict[str, tuple[str, ...]] = {
    kind: tuple(f.name for f in fields if _is_child(f.type)) for kind, fields in KIND_FIELDS.items()
}

#: Each kind's binding slots: the members typed ``Binding<_>``.
BINDING_SLOTS: dict[str, tuple[str, ...]] = {
    kind: tuple(f.name for f in fields if f.type.kind == "union" and f.type.name == "Binding")
    for kind, fields in KIND_FIELDS.items()
}
