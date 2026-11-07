"""
Pinned Domato tables for reproducible HTML grammar coverage.

Copyright 2017 Google Inc. All Rights Reserved.
Licensed under the Apache License, Version 2.0 (the "License");
you may not use this file except in compliance with the License.
You may obtain a copy of the License at

   http://www.apache.org/licenses/LICENSE-2.0

Unless required by applicable law or agreed to in writing, software
distributed under the License is distributed on an "AS IS" BASIS,
WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
See the License for the specific language governing permissions and
limitations under the License.
"""

from __future__ import annotations

from typing import Final, TypeAlias

TableRule: TypeAlias = tuple[int, str, str]


HTML_TAGS: Final[tuple[str, ...]] = (
    "a",
    "abbr",
    "acronym",
    "address",
    "applet",
    "area",
    "article",
    "aside",
    "audio",
    "b",
    "base",
    "basefont",
    "bdi",
    "bdo",
    "bgsound",
    "big",
    "blockquote",
    "br",
    "button",
    "canvas",
    "caption",
    "center",
    "cite",
    "code",
    "col",
    "colgroup",
    "command",
    "content",
    "data",
    "datalist",
    "dd",
    "del",
    "details",
    "dfn",
    "dialog",
    "dir",
    "div",
    "dl",
    "dt",
    "em",
    "embed",
    "fieldset",
    "figcaption",
    "figure",
    "font",
    "footer",
    "form",
    "frame",
    "frameset",
    "h1",
    "h2",
    "h3",
    "h4",
    "h5",
    "h6",
    "header",
    "hgroup",
    "hr",
    "i",
    "iframe",
    "image",
    "img",
    "input",
    "ins",
    "isindex",
    "kbd",
    "keygen",
    "label",
    "layer",
    "legend",
    "li",
    "link",
    "listing",
    "main",
    "map",
    "mark",
    "marquee",
    "menu",
    "menuitem",
    "meta",
    "meter",
    "nav",
    "nobr",
    "noembed",
    "noframes",
    "nolayer",
    "noscript",
    "object",
    "ol",
    "optgroup",
    "option",
    "output",
    "p",
    "param",
    "picture",
    "plaintext",
    "pre",
    "progress",
    "q",
    "rp",
    "rt",
    "ruby",
    "s",
    "samp",
    "section",
    "select",
    "shadow",
    "small",
    "source",
    "span",
    "strike",
    "strong",
    "style",
    "sub",
    "summary",
    "sup",
    "table",
    "tbody",
    "td",
    "template",
    "textarea",
    "tfoot",
    "th",
    "thead",
    "time",
    "title",
    "tr",
    "track",
    "tt",
    "u",
    "ul",
    "var",
    "video",
    "wbr",
    "xmp",
    "blink",
    "body",
    "element",
    "head",
    "html",
    "multicol",
    "rtc",
    "script",
    "spacer",
)

HTML_RULES: Final[tuple[TableRule, ...]] = (
    (17, "framesrc", "x"),
    (18, "framesrc", "data:text/html,foo"),
    (20, "attribute_imgsrc", 'src="<imgsrc>"'),
    (21, "attribute_videosrc", 'src="<videosrc>"'),
    (22, "attribute_audiosrc", 'src="<audiosrc>"'),
    (23, "attribute_framesrc", 'src="<framesrc>"'),
    (25, "inputtype", '"button"'),
    (26, "inputtype", '"checkbox"'),
    (27, "inputtype", '"color"'),
    (28, "inputtype", '"date"'),
    (29, "inputtype", '"datetime"'),
    (30, "inputtype", '"datetime-local"'),
    (31, "inputtype", '"emailNew"'),
    (32, "inputtype", '"file"'),
    (33, "inputtype", '"hidden"'),
    (34, "inputtype", '"image"'),
    (35, "inputtype", '"month"'),
    (36, "inputtype", '"number"'),
    (37, "inputtype", '"password"'),
    (38, "inputtype", '"radio"'),
    (39, "inputtype", '"range"'),
    (40, "inputtype", '"reset"'),
    (41, "inputtype", '"search"'),
    (42, "inputtype", '"submit"'),
    (43, "inputtype", '"tel"'),
    (44, "inputtype", '"text"'),
    (45, "inputtype", '"time"'),
    (46, "inputtype", '"url"'),
    (47, "inputtype", '"week"'),
    (
        51,
        "html",
        ("<lt>!-- saved from url=(0014)about:internet --<gt><lf><lt>html<gt><head><body><newline><lt>/html<gt>"),
    ),
    (
        52,
        "head",
        (
            "<newline><lt>head<gt><newline><lt>style<"
            "gt><newline>/*begincss*/<newline><lt>css"
            "fuzzer<gt>/*endcss*/<newline><lt>/style<"
            "gt><newline><lt>script<gt><newline><lt>j"
            "sfuzzer<gt><newline><lt>/script<gt><newl"
            "ine><lt>/head<gt>"
        ),
    ),
    (
        53,
        "body",
        (
            "<newline><lt>body onload=jsfuzzer()<gt><"
            "newline><lt>!--beginhtml--<gt><bodyeleme"
            "nts><lt>!--endhtml--<gt><newline><lt>/bo"
            "dy<gt>"
        ),
    ),
    (55, "attributes", "<attribute> <attribute> <attribute> <attribute> <attribute>"),
    (
        57,
        "bodyelements",
        (
            "<newline><element><newline><element><new"
            "line><element><newline><element><newline"
            "><element><newline><element><newline><el"
            "ement><newline><element><newline><elemen"
            "t><newline><element><newline>"
        ),
    ),
    (58, "headelements", "<htmlsafestring min=32 max=126>"),
    (60, "innerelements", "<htmlsafestring min=32 max=126>"),
    (61, "innerelements", "<newline><element><newline>"),
    (62, "innerelements", "<newline><element><newline><element><newline>"),
    (64, "innerformelements", "<htmlsafestring min=32 max=126>"),
    (65, "formchildren", "<newline><formchildelement><newline>"),
    (66, "formchildren", "<newline><formchildelement><newline><formchildelement><newline>"),
    (68, "selectchildren", "<optionelements>"),
    (69, "selectchildren", "<optgroupelements>"),
    (71, "optgroupelements", "<newline><HTMLOptGroupElement><newline>"),
    (72, "optgroupelements", "<newline><HTMLOptGroupElement><newline><HTMLOptGroupElement><newline>"),
    (74, "optionelements", "<htmlsafestring min=32 max=126>"),
    (75, "optionelements", "<newline><HTMLOptionElement><newline>"),
    (76, "optionelements", "<newline><HTMLOptionElement><newline><HTMLOptionElement><newline>"),
    (78, "areaelements", "<htmlsafestring min=32 max=126>"),
    (79, "areaelements", "<newline><HTMLAreaElement><newline>"),
    (80, "areaelements", "<newline><HTMLAreaElement><newline><HTMLAreaElement><newline>"),
    (82, "tablechildren", "<htmlsafestring min=32 max=126>"),
    (83, "tablechildren", "<tablechild>"),
    (84, "tablechildren", "<newline><tablechild><newline><tablechild><newline>"),
    (85, "tablechildren", "<newline><tablechild><newline><tablechild><newline><tablechild><newline>"),
    (
        86,
        "tablechildren",
        ("<newline><tablechild><newline><tablechild><newline><tablechild><newline><tablechild><newline>"),
    ),
    (88, "tablechild", "<HTMLTableCaptionElement>"),
    (89, "tablechild", "<HTMLTableSectionElement>"),
    (90, "tablechild", "<colgroupelement>"),
    (91, "tablechild", "<HTMLTableRowElement>"),
    (92, "tablechild", "<HTMLTableRowElement>"),
    (93, "tablechild", "<element>"),
    (95, "colelements", "<htmlsafestring min=32 max=126>"),
    (96, "colelements", "<newline><HTMLTableColElement><newline>"),
    (97, "colelements", "<newline><HTMLTableColElement><newline><HTMLTableColElement><newline>"),
    (99, "trelements", "<htmlsafestring min=32 max=126>"),
    (100, "trelements", "<newline><HTMLTableRowElement><newline>"),
    (101, "trelements", "<newline><HTMLTableRowElement><newline><HTMLTableRowElement><newline>"),
    (103, "thelements", "<htmlsafestring min=32 max=126>"),
    (104, "thelements", "<newline><HTMLTableCellElement><newline>"),
    (105, "thelements", "<newline><HTMLTableCellElement><newline><HTMLTableCellElement><newline>"),
    (107, "lielements", "<htmlsafestring min=32 max=126>"),
    (108, "lielements", "<newline><HTMLLIElement><newline>"),
    (109, "lielements", "<newline><HTMLLIElement><newline><HTMLLIElement><newline>"),
    (111, "formchildelement", "<HTMLLegendElement>"),
    (112, "formchildelement", "<HTMLInputElement>"),
    (113, "formchildelement", "<HTMLTextAreaElement>"),
    (114, "formchildelement", "<HTMLButtonElement>"),
    (115, "formchildelement", "<HTMLLegendElement>"),
    (116, "formchildelement", "<HTMLKeygenElement>"),
    (117, "formchildelement", "<HTMLObjectElement>"),
    (118, "formchildelement", "<HTMLSelectElement>"),
    (119, "formchildelement", "<HTMLOutputElement>"),
    (120, "formchildelement", "<HTMLLabelElement>"),
    (121, "formchildelement", "<HTMLFieldSetElement>"),
    (122, "formchildelement", "<HTMLOptionElement>"),
    (123, "formchildelement", "<HTMLDataListElement>"),
    (124, "formchildelement", "<element>"),
    (126, "detailchildren", "<innerelements>"),
    (127, "detailchildren", "<newline><summaryelement><newline><innerelements>"),
    (129, "dlchildren", "<htmlsafestring min=32 max=126>"),
    (130, "dlchildren", "<newline><dlchild><newline><dlchild><newline>"),
    (131, "dlchildren", ("<newline><dlchild><newline><dlchild><newline><dlchild><newline><dlchild><newline>")),
    (133, "frames", "<htmlsafestring min=32 max=126>"),
    (134, "frames", "<newline><HTMLFrameElement><newline>"),
    (135, "frames", "<newline><HTMLFrameElement><newline><HTMLFrameElement><newline>"),
    (137, "dlchild", "<dtelement>"),
    (138, "dlchild", "<ddelement>"),
    (140, "menuchildren", "<htmlsafestring min=32 max=126>"),
    (141, "menuchildren", "<newline><menuchild><newline>"),
    (142, "menuchildren", "<newline><menuchild><newline><menuchild><newline>"),
    (144, "menuchild", "<menuitemelement>"),
    (145, "menuchild", "<HTMLMenuElement>"),
    (147, "paramelements", "<htmlsafestring min=32 max=126>"),
    (148, "paramelements", "<newline><HTMLParamElement><newline>"),
    (149, "paramelements", "<newline><HTMLParamElement><newline><HTMLParamElement><newline>"),
    (151, "mediachildren", "<htmlsafestring min=32 max=126>"),
    (152, "mediachildren", "<newline><mediachild><newline>"),
    (153, "mediachildren", "<newline><mediachild><newline><mediachild><newline>"),
    (155, "mediachild", "<htmlsafestring min=32 max=126>"),
    (156, "mediachild", "<HTMLSourceElement>"),
    (157, "mediachild", "<HTMLTrackElement>"),
    (158, "mediachild", "<element>"),
    (160, "eventhandler", "eventhandler1()"),
    (161, "eventhandler", "eventhandler2()"),
    (162, "eventhandler", "eventhandler3()"),
    (163, "eventhandler", "eventhandler4()"),
    (164, "eventhandler", "eventhandler5()"),
    (166, "element", "<HTMLAnchorElement>"),
    (169, "element", "<HTMLBaseElement>"),
    (170, "element", "<HTMLBaseFontElement>"),
    (171, "element", "<HTMLBRElement>"),
    (172, "element", "<HTMLButtonElement>"),
    (173, "element", "<HTMLCanvasElement>"),
    (174, "element", "<HTMLDataElement>"),
    (175, "element", "<HTMLDataListElement>"),
    (176, "element", "<HTMLDetailsElement>"),
    (177, "element", "<HTMLDialogElement>"),
    (178, "element", "<HTMLDirectoryElement>"),
    (179, "element", "<HTMLDivElement>"),
    (180, "element", "<HTMLDListElement>"),
    (181, "element", "<HTMLEmbedElement>"),
    (183, "element", "<HTMLFontElement>"),
    (184, "element", "<HTMLFormElement>"),
    (187, "element", "<HTMLHeadingElement>"),
    (188, "element", "<HTMLHRElement>"),
    (189, "element", "<HTMLIFrameElement>"),
    (190, "element", "<HTMLImageElement>"),
    (191, "element", "<HTMLInputElement>"),
    (192, "element", "<HTMLKeygenElement>"),
    (193, "element", "<HTMLLabelElement>"),
    (196, "element", "<HTMLLinkElement>"),
    (197, "element", "<HTMLMapElement>"),
    (198, "element", "<HTMLMarqueeElement>"),
    (199, "element", "<HTMLAudioElement>"),
    (200, "element", "<HTMLMenuElement>"),
    (201, "element", "<HTMLMetaElement>"),
    (202, "element", "<HTMLMeterElement>"),
    (203, "element", "<HTMLModElement>"),
    (205, "element", "<HTMLOListElement>"),
    (208, "element", "<HTMLOutputElement>"),
    (209, "element", "<HTMLParagraphElement>"),
    (211, "element", "<HTMLPreElement>"),
    (212, "element", "<HTMLProgressElement>"),
    (213, "element", "<HTMLQuoteElement>"),
    (215, "element", "<HTMLSelectElement>"),
    (217, "element", "<HTMLSpanElement>"),
    (218, "element", "<HTMLStyleElement>"),
    (222, "element", "<HTMLTableElement>"),
    (225, "element", "<HTMLTemplateElement>"),
    (226, "element", "<HTMLTextAreaElement>"),
    (227, "element", "<HTMLTimeElement>"),
    (228, "element", "<HTMLTitleElement>"),
    (230, "element", "<HTMLUListElement>"),
    (231, "element", "<HTMLVideoElement>"),
    (232, "element", "<HTMLContentElement>"),
    (233, "element", "<HTMLShadowElement>"),
    (236, "element", "<svgelement_svg>"),
    (237, "element", "<mathmlelement_math>"),
    (239, "element", "<otherelement>"),
    (240, "element", "<otherelement>"),
    (242, "attribute", "<attribute_abbr>"),
    (243, "attribute", "<attribute_accept>"),
    (244, "attribute", "<attribute_accesskey>"),
    (245, "attribute", "<attribute_accumulate>"),
    (246, "attribute", "<attribute_additive>"),
    (247, "attribute", "<attribute_align>"),
    (248, "attribute", "<attribute_alink>"),
    (249, "attribute", "<attribute_allowfullscreen>"),
    (250, "attribute", "<attribute_alt>"),
    (251, "attribute", "<attribute_archive>"),
    (252, "attribute", "<attribute_as>"),
    (253, "attribute", "<attribute_async>"),
    (254, "attribute", "<attribute_autocomplete>"),
    (255, "attribute", "<attribute_autofocus>"),
    (256, "attribute", "<attribute_autoload>"),
    (257, "attribute", "<attribute_autoplay>"),
    (258, "attribute", "<attribute_axis>"),
    (259, "attribute", "<attribute_azimuth>"),
    (260, "attribute", "<attribute_background>"),
    (261, "attribute", "<attribute_basefrequency>"),
    (262, "attribute", "<attribute_baseprofile>"),
    (263, "attribute", "<attribute_behavior>"),
    (264, "attribute", "<attribute_bgcolor>"),
    (265, "attribute", "<attribute_bgproperties>"),
    (266, "attribute", "<attribute_border>"),
    (267, "attribute", "<attribute_bordercolor>"),
    (268, "attribute", "<attribute_case>"),
    (269, "attribute", "<attribute_capture>"),
    (270, "attribute", "<attribute_cellpadding>"),
    (271, "attribute", "<attribute_cellspacing>"),
    (272, "attribute", "<attribute_challenge>"),
    (273, "attribute", "<attribute_char>"),
    (274, "attribute", "<attribute_charoff>"),
    (275, "attribute", "<attribute_charset>"),
    (276, "attribute", "<attribute_checked>"),
    (277, "attribute", "<attribute_cite>"),
    (278, "attribute", "<attribute_class>"),
    (279, "attribute", "<attribute_classid>"),
    (280, "attribute", "<attribute_clear>"),
    (281, "attribute", "<attribute_code>"),
    (282, "attribute", "<attribute_codebase>"),
    (283, "attribute", "<attribute_codetype>"),
    (284, "attribute", "<attribute_color>"),
    (285, "attribute", "<attribute_cols>"),
    (286, "attribute", "<attribute_colspan>"),
    (287, "attribute", "<attribute_compact>"),
    (288, "attribute", "<attribute_content>"),
    (289, "attribute", "<attribute_contenteditable>"),
    (290, "attribute", "<attribute_contextmenu>"),
    (291, "attribute", "<attribute_controls>"),
    (292, "attribute", "<attribute_coords>"),
    (293, "attribute", "<attribute_crossorigin>"),
    (294, "attribute", "<attribute_data>"),
    (295, "attribute", "<attribute_datetime>"),
    (296, "attribute", "<attribute_declare>"),
    (297, "attribute", "<attribute_default>"),
    (298, "attribute", "<attribute_defer>"),
    (299, "attribute", "<attribute_desc>"),
    (300, "attribute", "<attribute_description>"),
    (301, "attribute", "<attribute_dir>"),
    (302, "attribute", "<attribute_direction>"),
    (303, "attribute", "<attribute_dirname>"),
    (304, "attribute", "<attribute_disabled>"),
    (305, "attribute", "<attribute_display>"),
    (306, "attribute", "<attribute_disposition>"),
    (307, "attribute", "<attribute_download>"),
    (308, "attribute", "<attribute_draggable>"),
    (309, "attribute", "<attribute_encoding>"),
    (310, "attribute", "<attribute_enctype>"),
    (311, "attribute", "<attribute_expanded>"),
    (312, "attribute", "<attribute_face>"),
    (313, "attribute", "<attribute_focus>"),
    (314, "attribute", "<attribute_focused>"),
    (315, "attribute", "<attribute_for>"),
    (316, "attribute", "<attribute_form>"),
    (317, "attribute", "<attribute_formaction>"),
    (318, "attribute", "<attribute_formenctype>"),
    (319, "attribute", "<attribute_formmethod>"),
    (320, "attribute", "<attribute_formnovalidate>"),
    (321, "attribute", "<attribute_formtarget>"),
    (322, "attribute", "<attribute_frame>"),
    (323, "attribute", "<attribute_frameborder>"),
    (324, "attribute", "<attribute_framemargin>"),
    (325, "attribute", "<attribute_framespacing>"),
    (326, "attribute", "<attribute_headers>"),
    (327, "attribute", "<attribute_height>"),
    (328, "attribute", "<attribute_hidden>"),
    (329, "attribute", "<attribute_high>"),
    (330, "attribute", "<attribute_href>"),
    (331, "attribute", "<attribute_hreflang>"),
    (332, "attribute", "<attribute_hspace>"),
    (333, "attribute", "<attribute_icon>"),
    (334, "attribute", "<attribute_incremental>"),
    (335, "attribute", "<attribute_indeterminate>"),
    (336, "attribute", "<attribute_inner>"),
    (337, "attribute", "<attribute_inputmode>"),
    (338, "attribute", "<attribute_is>"),
    (339, "attribute", "<attribute_ismap>"),
    (340, "attribute", "<attribute_item>"),
    (341, "attribute", "<attribute_itemid>"),
    (342, "attribute", "<attribute_itemprop>"),
    (343, "attribute", "<attribute_itemref>"),
    (344, "attribute", "<attribute_itemscope>"),
    (345, "attribute", "<attribute_itemtype>"),
    (346, "attribute", "<attribute_keytype>"),
    (347, "attribute", "<attribute_kind>"),
    (348, "attribute", "<attribute_label>"),
    (349, "attribute", "<attribute_lang>"),
    (350, "attribute", "<attribute_language>"),
    (351, "attribute", "<attribute_layout>"),
    (352, "attribute", "<attribute_left>"),
    (353, "attribute", "<attribute_leftmargin>"),
    (354, "attribute", "<attribute_link>"),
    (355, "attribute", "<attribute_list>"),
    (356, "attribute", "<attribute_longdesc>"),
    (357, "attribute", "<attribute_loop>"),
    (358, "attribute", "<attribute_loopend>"),
    (359, "attribute", "<attribute_loopstart>"),
    (360, "attribute", "<attribute_low>"),
    (361, "attribute", "<attribute_lowsrc>"),
    (362, "attribute", "<attribute_manifest>"),
    (363, "attribute", "<attribute_margin>"),
    (364, "attribute", "<attribute_marginheight>"),
    (365, "attribute", "<attribute_marginwidth>"),
    (366, "attribute", "<attribute_max>"),
    (367, "attribute", "<attribute_maxlength>"),
    (368, "attribute", "<attribute_mayscript>"),
    (369, "attribute", "<attribute_media>"),
    (370, "attribute", "<attribute_menu>"),
    (371, "attribute", "<attribute_method>"),
    (372, "attribute", "<attribute_min>"),
    (373, "attribute", "<attribute_minlength>"),
    (374, "attribute", "<attribute_mode>"),
    (375, "attribute", "<attribute_multiple>"),
    (376, "attribute", "<attribute_muted>"),
    (377, "attribute", "<attribute_name>"),
    (378, "attribute", "<attribute_nohref>"),
    (379, "attribute", "<attribute_nonce>"),
    (380, "attribute", "<attribute_noresize>"),
    (381, "attribute", "<attribute_noshade>"),
    (382, "attribute", "<attribute_novalidate>"),
    (383, "attribute", "<attribute_nowrap>"),
    (384, "attribute", "<attribute_open>"),
    (385, "attribute", "<attribute_optimum>"),
    (386, "attribute", "<attribute_pattern>"),
    (387, "attribute", "<attribute_ping>"),
    (388, "attribute", "<attribute_placeholder>"),
    (389, "attribute", "<attribute_playcount>"),
    (390, "attribute", "<attribute_pluginspage>"),
    (391, "attribute", "<attribute_poster>"),
    (392, "attribute", "<attribute_preload>"),
    (393, "attribute", "<attribute_profile>"),
    (394, "attribute", "<attribute_prompt>"),
    (395, "attribute", "<attribute_quality>"),
    (396, "attribute", "<attribute_radiogroup>"),
    (397, "attribute", "<attribute_readonly>"),
    (398, "attribute", "<attribute_ref>"),
    (399, "attribute", "<attribute_referrerpolicy>"),
    (400, "attribute", "<attribute_rel>"),
    (401, "attribute", "<attribute_required>"),
    (402, "attribute", "<attribute_results>"),
    (403, "attribute", "<attribute_rev>"),
    (404, "attribute", "<attribute_reversed>"),
    (405, "attribute", "<attribute_right>"),
    (406, "attribute", "<attribute_rightmargin>"),
    (407, "attribute", "<attribute_role>"),
    (408, "attribute", "<attribute_row>"),
    (409, "attribute", "<attribute_rows>"),
    (410, "attribute", "<attribute_rowspan>"),
    (411, "attribute", "<attribute_rules>"),
    (412, "attribute", "<attribute_sandbox>"),
    (413, "attribute", "<attribute_scheme>"),
    (414, "attribute", "<attribute_scope>"),
    (415, "attribute", "<attribute_scoped>"),
    (416, "attribute", "<attribute_scrollamount>"),
    (417, "attribute", "<attribute_scrolldelay>"),
    (418, "attribute", "<attribute_scrolling>"),
    (419, "attribute", "<attribute_seamless>"),
    (420, "attribute", "<attribute_seed>"),
    (421, "attribute", "<attribute_select>"),
    (422, "attribute", "<attribute_selected>"),
    (423, "attribute", "<attribute_shape>"),
    (424, "attribute", "<attribute_shouldfocus>"),
    (425, "attribute", "<attribute_size>"),
    (426, "attribute", "<attribute_sizes>"),
    (427, "attribute", "<attribute_slope>"),
    (428, "attribute", "<attribute_slot>"),
    (429, "attribute", "<attribute_span>"),
    (430, "attribute", "<attribute_spellcheck>"),
    (431, "attribute", "<attribute_src>"),
    (432, "attribute", "<attribute_srcdoc>"),
    (433, "attribute", "<attribute_srcset>"),
    (434, "attribute", "<attribute_srclang>"),
    (435, "attribute", "<attribute_standby>"),
    (436, "attribute", "<attribute_start>"),
    (437, "attribute", "<attribute_startoffset>"),
    (438, "attribute", "<attribute_startval>"),
    (439, "attribute", "<attribute_step>"),
    (440, "attribute", "<attribute_style>"),
    (441, "attribute", "<attribute_summary>"),
    (442, "attribute", "<attribute_tabindex>"),
    (443, "attribute", "<attribute_target>"),
    (444, "attribute", "<attribute_text>"),
    (445, "attribute", "<attribute_title>"),
    (446, "attribute", "<attribute_topmargin>"),
    (447, "attribute", "<attribute_translate>"),
    (448, "attribute", "<attribute_truespeed>"),
    (449, "attribute", "<attribute_type>"),
    (450, "attribute", "<attribute_usemap>"),
    (451, "attribute", "<attribute_valign>"),
    (452, "attribute", "<attribute_value>"),
    (453, "attribute", "<attribute_valuetype>"),
    (454, "attribute", "<attribute_version>"),
    (455, "attribute", "<attribute_vlink>"),
    (456, "attribute", "<attribute_vspace>"),
    (457, "attribute", "<attribute_width>"),
    (458, "attribute", "<attribute_wrap>"),
    (460, "attribute", "<attribute_eventhandler>"),
    (461, "attribute", "<attribute_eventhandler>"),
    (462, "attribute", "<attribute_eventhandler>"),
    (463, "attribute", "<attribute_eventhandler>"),
    (464, "attribute", "<attribute_eventhandler>"),
    (465, "attribute", "<attribute_eventhandler>"),
    (466, "attribute", "<attribute_eventhandler>"),
    (467, "attribute", "<attribute_eventhandler>"),
    (468, "attribute", "<attribute_eventhandler>"),
    (469, "attribute", "<attribute_eventhandler>"),
    (470, "attribute", "<attribute_eventhandler>"),
    (472, "attribute_eventhandler", "<attribute_onabort>"),
    (473, "attribute_eventhandler", "<attribute_onautocomplete>"),
    (474, "attribute_eventhandler", "<attribute_onautocompleteerror>"),
    (475, "attribute_eventhandler", "<attribute_onafterscriptexecute>"),
    (476, "attribute_eventhandler", "<attribute_onanimationend>"),
    (477, "attribute_eventhandler", "<attribute_onanimationiteration>"),
    (478, "attribute_eventhandler", "<attribute_onanimationstart>"),
    (479, "attribute_eventhandler", "<attribute_onbeforecopy>"),
    (480, "attribute_eventhandler", "<attribute_onbeforecut>"),
    (481, "attribute_eventhandler", "<attribute_onbeforeload>"),
    (482, "attribute_eventhandler", "<attribute_onbeforepaste>"),
    (483, "attribute_eventhandler", "<attribute_onbeforescriptexecute>"),
    (484, "attribute_eventhandler", "<attribute_onbeforeunload>"),
    (485, "attribute_eventhandler", "<attribute_onbegin>"),
    (486, "attribute_eventhandler", "<attribute_onblur>"),
    (487, "attribute_eventhandler", "<attribute_oncanplay>"),
    (488, "attribute_eventhandler", "<attribute_oncanplaythrough>"),
    (489, "attribute_eventhandler", "<attribute_onchange>"),
    (490, "attribute_eventhandler", "<attribute_onclick>"),
    (491, "attribute_eventhandler", "<attribute_oncontextmenu>"),
    (492, "attribute_eventhandler", "<attribute_oncopy>"),
    (493, "attribute_eventhandler", "<attribute_oncut>"),
    (494, "attribute_eventhandler", "<attribute_ondblclick>"),
    (495, "attribute_eventhandler", "<attribute_ondrag>"),
    (496, "attribute_eventhandler", "<attribute_ondragend>"),
    (497, "attribute_eventhandler", "<attribute_ondragenter>"),
    (498, "attribute_eventhandler", "<attribute_ondragleave>"),
    (499, "attribute_eventhandler", "<attribute_ondragover>"),
    (500, "attribute_eventhandler", "<attribute_ondragstart>"),
    (501, "attribute_eventhandler", "<attribute_ondrop>"),
    (502, "attribute_eventhandler", "<attribute_ondurationchange>"),
    (503, "attribute_eventhandler", "<attribute_onend>"),
    (504, "attribute_eventhandler", "<attribute_onemptied>"),
    (505, "attribute_eventhandler", "<attribute_onended>"),
    (506, "attribute_eventhandler", "<attribute_onerror>"),
    (507, "attribute_eventhandler", "<attribute_onfocus>"),
    (508, "attribute_eventhandler", "<attribute_onfocusin>"),
    (509, "attribute_eventhandler", "<attribute_onfocusout>"),
    (510, "attribute_eventhandler", "<attribute_onhashchange>"),
    (511, "attribute_eventhandler", "<attribute_oninput>"),
    (512, "attribute_eventhandler", "<attribute_oninvalid>"),
    (513, "attribute_eventhandler", "<attribute_onkeydown>"),
    (514, "attribute_eventhandler", "<attribute_onkeypress>"),
    (515, "attribute_eventhandler", "<attribute_onkeyup>"),
    (516, "attribute_eventhandler", "<attribute_onload>"),
    (517, "attribute_eventhandler", "<attribute_onloadeddata>"),
    (518, "attribute_eventhandler", "<attribute_onloadedmetadata>"),
    (519, "attribute_eventhandler", "<attribute_onloadstart>"),
    (520, "attribute_eventhandler", "<attribute_onmessage>"),
    (521, "attribute_eventhandler", "<attribute_onmousedown>"),
    (522, "attribute_eventhandler", "<attribute_onmouseenter>"),
    (523, "attribute_eventhandler", "<attribute_onmouseleave>"),
    (524, "attribute_eventhandler", "<attribute_onmousemove>"),
    (525, "attribute_eventhandler", "<attribute_onmouseout>"),
    (526, "attribute_eventhandler", "<attribute_onmouseover>"),
    (527, "attribute_eventhandler", "<attribute_onmouseup>"),
    (528, "attribute_eventhandler", "<attribute_onmousewheel>"),
    (529, "attribute_eventhandler", "<attribute_onoffline>"),
    (530, "attribute_eventhandler", "<attribute_ononline>"),
    (531, "attribute_eventhandler", "<attribute_onorientationchange>"),
    (532, "attribute_eventhandler", "<attribute_onpagehide>"),
    (533, "attribute_eventhandler", "<attribute_onpageshow>"),
    (534, "attribute_eventhandler", "<attribute_onpaste>"),
    (535, "attribute_eventhandler", "<attribute_onpause>"),
    (536, "attribute_eventhandler", "<attribute_onplay>"),
    (537, "attribute_eventhandler", "<attribute_onplaying>"),
    (538, "attribute_eventhandler", "<attribute_onpopstate>"),
    (539, "attribute_eventhandler", "<attribute_onprogress>"),
    (540, "attribute_eventhandler", "<attribute_onratechange>"),
    (541, "attribute_eventhandler", "<attribute_onreset>"),
    (542, "attribute_eventhandler", "<attribute_onresize>"),
    (543, "attribute_eventhandler", "<attribute_onscroll>"),
    (544, "attribute_eventhandler", "<attribute_onsearch>"),
    (545, "attribute_eventhandler", "<attribute_onseeked>"),
    (546, "attribute_eventhandler", "<attribute_onseeking>"),
    (547, "attribute_eventhandler", "<attribute_onselect>"),
    (548, "attribute_eventhandler", "<attribute_onselectionchange>"),
    (549, "attribute_eventhandler", "<attribute_onselectstart>"),
    (550, "attribute_eventhandler", "<attribute_onstalled>"),
    (551, "attribute_eventhandler", "<attribute_onstorage>"),
    (552, "attribute_eventhandler", "<attribute_onsubmit>"),
    (553, "attribute_eventhandler", "<attribute_onsuspend>"),
    (554, "attribute_eventhandler", "<attribute_ontimeupdate>"),
    (555, "attribute_eventhandler", "<attribute_ontoggle>"),
    (556, "attribute_eventhandler", "<attribute_ontouchcancel>"),
    (557, "attribute_eventhandler", "<attribute_ontouchend>"),
    (558, "attribute_eventhandler", "<attribute_ontouchmove>"),
    (559, "attribute_eventhandler", "<attribute_ontouchstart>"),
    (560, "attribute_eventhandler", "<attribute_ontransitionend>"),
    (561, "attribute_eventhandler", "<attribute_onunload>"),
    (562, "attribute_eventhandler", "<attribute_onvolumechange>"),
    (563, "attribute_eventhandler", "<attribute_onwaiting>"),
    (564, "attribute_eventhandler", "<attribute_onwebkitanimationend>"),
    (565, "attribute_eventhandler", "<attribute_onwebkitanimationiteration>"),
    (566, "attribute_eventhandler", "<attribute_onwebkitanimationstart>"),
    (567, "attribute_eventhandler", "<attribute_onwebkitfullscreenchange>"),
    (568, "attribute_eventhandler", "<attribute_onwebkitfullscreenerror>"),
    (569, "attribute_eventhandler", "<attribute_onwebkitkeyadded>"),
    (570, "attribute_eventhandler", "<attribute_onwebkitkeyerror>"),
    (571, "attribute_eventhandler", "<attribute_onwebkitkeymessage>"),
    (572, "attribute_eventhandler", "<attribute_onwebkitneedkey>"),
    (573, "attribute_eventhandler", "<attribute_onwebkitsourceclose>"),
    (574, "attribute_eventhandler", "<attribute_onwebkitsourceended>"),
    (575, "attribute_eventhandler", "<attribute_onwebkitsourceopen>"),
    (576, "attribute_eventhandler", "<attribute_onwebkitspeechchange>"),
    (577, "attribute_eventhandler", "<attribute_onwebkittransitionend>"),
    (578, "attribute_eventhandler", "<attribute_onwheel>"),
    (580, "attribute_abbr", 'abbr="<abbr_value>"'),
    (581, "attribute_accept", 'accept="<accept_value>"'),
    (582, "attribute_accept-charset", 'accept-charset="<accept-charset_value>"'),
    (583, "attribute_accepts-touch", 'accepts-touch="<accepts-touch_value>"'),
    (584, "attribute_accesskey", 'accesskey="<accesskey_value>"'),
    (585, "attribute_accumulate", 'accumulate="<accumulate_value>"'),
    (587, "attribute_additive", 'additive="<additive_value>"'),
    (588, "attribute_align", 'align="<align_value>"'),
    (589, "attribute_alink", 'alink="<alink_value>"'),
    (590, "attribute_allowfullscreen", 'allowfullscreen="<allowfullscreen_value>"'),
    (591, "attribute_alt", 'alt="<alt_value>"'),
    (592, "attribute_archive", 'archive="<archive_value>"'),
    (593, "attribute_aria-activedescendant", 'aria-activedescendant="<aria-activedescendant_value>"'),
    (594, "attribute_aria-autocomplete", 'aria-autocomplete="<aria-autocomplete_value>"'),
    (595, "attribute_aria-atomic", 'aria-atomic="<aria-atomic_value>"'),
    (596, "attribute_aria-busy", 'aria-busy="<aria-busy_value>"'),
    (597, "attribute_aria-checked", 'aria-checked="<aria-checked_value>"'),
    (598, "attribute_aria-controls", 'aria-controls="<aria-controls_value>"'),
    (599, "attribute_aria-describedby", 'aria-describedby="<aria-describedby_value>"'),
    (600, "attribute_aria-disabled", 'aria-disabled="<aria-disabled_value>"'),
    (601, "attribute_aria-dropeffect", 'aria-dropeffect="<aria-dropeffect_value>"'),
    (602, "attribute_aria-expanded", 'aria-expanded="<aria-expanded_value>"'),
    (603, "attribute_aria-flowto", 'aria-flowto="<aria-flowto_value>"'),
    (604, "attribute_aria-grabbed", 'aria-grabbed="<aria-grabbed_value>"'),
    (605, "attribute_aria-haspopup", 'aria-haspopup="<aria-haspopup_value>"'),
    (606, "attribute_aria-help", 'aria-help="<aria-help_value>"'),
    (607, "attribute_aria-hidden", 'aria-hidden="<aria-hidden_value>"'),
    (608, "attribute_aria-invalid", 'aria-invalid="<aria-invalid_value>"'),
    (609, "attribute_aria-label", 'aria-label="<aria-label_value>"'),
    (610, "attribute_aria-labeledby", 'aria-labeledby="<aria-labeledby_value>"'),
    (611, "attribute_aria-labelledby", 'aria-labelledby="<aria-labelledby_value>"'),
    (612, "attribute_aria-level", 'aria-level="<aria-level_value>"'),
    (613, "attribute_aria-live", 'aria-multiline="<aria-live_value>"'),
    (614, "attribute_aria-multiline", 'aria-multiline="<aria-multiline_value>"'),
    (615, "attribute_aria-multiselectable", 'aria-multiselectable="<aria-multiselectable_value>"'),
    (616, "attribute_aria-name", 'aria-name="<aria-name_value>"'),
    (617, "attribute_aria-orientation", 'aria-orientation="<aria-orientation_value>"'),
    (618, "attribute_aria-owns", 'aria-owns="<aria-owns_value>"'),
    (619, "attribute_aria-posinset", 'aria-posinset="<aria-posinset_value>"'),
    (620, "attribute_aria-pressed", 'aria-pressed="<aria-pressed_value>"'),
    (621, "attribute_aria-readonly", 'aria-readonly="<aria-readonly_value>"'),
    (622, "attribute_aria-relevant", 'aria-relevant="<aria-relevant_value>"'),
    (623, "attribute_aria-required", 'aria-required="<aria-required_value>"'),
    (624, "attribute_aria-selected", 'aria-selected="<aria-selected_value>"'),
    (625, "attribute_aria-setsize", 'aria-setsize="<aria-setsize_value>"'),
    (626, "attribute_aria-sort", 'aria-sort="<aria-sort_value>"'),
    (627, "attribute_aria-valuemax", 'aria-valuemax="<aria-valuemax_value>"'),
    (628, "attribute_aria-valuemin", 'aria-valuemin="<aria-valuemin_value>"'),
    (629, "attribute_aria-valuenow", 'aria-valuenow="<aria-valuenow_value>"'),
    (630, "attribute_aria-valuetext", 'aria-valuetext="<aria-valuetext_value>"'),
    (631, "attribute_as", 'as="<as_value>"'),
    (632, "attribute_async", 'async="<async_value>"'),
    (633, "attribute_autocomplete", 'autocomplete="<autocomplete_value>"'),
    (634, "attribute_autofocus", 'autofocus="<autofocus_value>"'),
    (635, "attribute_autoload", 'autoload="<autoload_value>"'),
    (636, "attribute_autoplay", 'autoplay="<autoplay_value>"'),
    (637, "attribute_axis", 'axis="<axis_value>"'),
    (638, "attribute_azimuth", 'azimuth="<azimuth_value>"'),
    (639, "attribute_background", 'background="<background_value>"'),
    (640, "attribute_background-color", 'background-color="<background-color_value>"'),
    (641, "attribute_basefrequency", 'basefrequency="<basefrequency_value>"'),
    (642, "attribute_baseprofile", 'baseprofile="<baseprofile_value>"'),
    (643, "attribute_behavior", 'behavior="<behavior_value>"'),
    (644, "attribute_bgcolor", 'bgcolor="<bgcolor_value>"'),
    (645, "attribute_bgproperties", 'bgproperties="<bgproperties_value>"'),
    (646, "attribute_border", 'border="<border_value>"'),
    (647, "attribute_bordercolor", 'bordercolor="<bordercolor_value>"'),
    (648, "attribute_buffered-rendering", 'buffered-rendering="<buffered-rendering_value>"'),
    (649, "attribute_can-process-drag", 'can-process-drag="<can-process-drag_value>"'),
    (650, "attribute_case", 'case="<case_value>"'),
    (651, "attribute_capture", 'case="<capture_value>"'),
    (652, "attribute_cellpadding", 'cellpadding="<cellpadding_value>"'),
    (653, "attribute_cellspacing", 'cellspacing="<cellspacing_value>"'),
    (654, "attribute_challenge", 'challenge="<challenge_value>"'),
    (655, "attribute_char", 'char="<char_value>"'),
    (656, "attribute_charoff", 'charoff="<charoff_value>"'),
    (657, "attribute_charset", 'charset="<charset_value>"'),
    (658, "attribute_checked", 'checked="<checked_value>"'),
    (659, "attribute_cite", 'cite="<cite_value>"'),
    (660, "attribute_class", 'class="<class_value>"'),
    (661, "attribute_classid", 'classid="<classid_value>"'),
    (662, "attribute_clear", 'clear="<clear_value>"'),
    (663, "attribute_code", 'code="<code_value>"'),
    (664, "attribute_codebase", 'codebase="<codebase_value>"'),
    (665, "attribute_codetype", 'codetype="<codetype_value>"'),
    (666, "attribute_color", 'color="<color_value>"'),
    (667, "attribute_cols", 'cols="<cols_value>"'),
    (668, "attribute_colspan", 'colspan="<colspan_value>"'),
    (669, "attribute_compact", 'compact="<compact_value>"'),
    (670, "attribute_content", 'content="<content_value>"'),
    (671, "attribute_contenteditable", 'contenteditable="<contenteditable_value>"'),
    (672, "attribute_contextmenu", 'contextmenu="<contextmenu_value>"'),
    (673, "attribute_controls", 'controls="<controls_value>"'),
    (674, "attribute_coords", 'coords="<coords_value>"'),
    (675, "attribute_crossorigin", 'crossorigin="<crossorigin_value>"'),
    (676, "attribute_data", 'data="<data_value>"'),
    (677, "attribute_datetime", 'datetime="<datetime_value>"'),
    (678, "attribute_declare", 'declare="<declare_value>"'),
    (679, "attribute_default", 'default="<default_value>"'),
    (680, "attribute_defer", 'defer="<defer_value>"'),
    (681, "attribute_desc", 'desc="<desc_value>"'),
    (682, "attribute_description", 'description="<description_value>"'),
    (683, "attribute_dir", 'dir="<dir_value>"'),
    (684, "attribute_direction", 'direction="<direction_value>"'),
    (685, "attribute_dirname", 'dirname="<dirname_value>"'),
    (686, "attribute_disabled", 'disabled="<disabled_value>"'),
    (687, "attribute_display", 'display="<display_value>"'),
    (688, "attribute_disposition", 'disposition="<disposition_value>"'),
    (689, "attribute_download", 'download="<download_value>"'),
    (690, "attribute_draggable", 'draggable="<draggable_value>"'),
    (691, "attribute_encoding", 'encoding="<encoding_value>"'),
    (692, "attribute_enctype", 'enctype="<enctype_value>"'),
    (693, "attribute_expanded", 'expanded="<expanded_value>"'),
    (694, "attribute_face", 'face="<face_value>"'),
    (695, "attribute_focus", 'focus="<focus_value>"'),
    (696, "attribute_focused", 'focused="<focused_value>"'),
    (697, "attribute_for", 'for="<for_value>"'),
    (698, "attribute_form", 'form="<form_value>"'),
    (699, "attribute_formaction", 'formaction="<formaction_value>"'),
    (700, "attribute_formenctype", 'formenctype="<formenctype_value>"'),
    (701, "attribute_formmethod", 'formmethod="<formmethod_value>"'),
    (702, "attribute_formnovalidate", 'formnovalidate="<formnovalidate_value>"'),
    (703, "attribute_formtarget", 'formtarget="<formtarget_value>"'),
    (704, "attribute_frame", 'frame="<frame_value>"'),
    (705, "attribute_frameborder", 'frameborder="<frameborder_value>"'),
    (706, "attribute_framemargin", 'framemargin="<framemargin_value>"'),
    (707, "attribute_framespacing", 'framespacing="<framespacing_value>"'),
    (708, "attribute_headers", 'headers="<headers_value>"'),
    (709, "attribute_height", 'height="<height_value>"'),
    (710, "attribute_hidden", 'hidden="<hidden_value>"'),
    (711, "attribute_high", 'high="<high_value>"'),
    (712, "attribute_href", 'href="<href_value>"'),
    (713, "attribute_hreflang", 'hreflang="<hreflang_value>"'),
    (714, "attribute_hspace", 'hspace="<hspace_value>"'),
    (715, "attribute_http-equiv", 'http-equiv="<http-equiv_value>"'),
    (716, "attribute_icon", 'icon="<icon_value>"'),
    (718, "attribute_incremental", 'incremental="<incremental_value>"'),
    (719, "attribute_indeterminate", 'indeterminate="<indeterminate_value>"'),
    (720, "attribute_inner", 'inner="<inner_value>"'),
    (721, "attribute_inputmode", 'inputmode="<inputmode_value>"'),
    (722, "attribute_is", 'is="<is_value>"'),
    (723, "attribute_ismap", 'ismap="<ismap_value>"'),
    (724, "attribute_item", 'item="<item_value>"'),
    (725, "attribute_itemid", 'itemid="<itemid_value>"'),
    (726, "attribute_itemprop", 'itemprop="<itemprop_value>"'),
    (727, "attribute_itemref", 'itemref="<itemref_value>"'),
    (728, "attribute_itemscope", 'itemscope="<itemscope_value>"'),
    (729, "attribute_itemtype", 'itemtype="<itemtype_value>"'),
    (730, "attribute_keytype", 'keytype="<keytype_value>"'),
    (731, "attribute_kind", 'kind="<kind_value>"'),
    (732, "attribute_label", 'label="<label_value>"'),
    (733, "attribute_lang", 'lang="<lang_value>"'),
    (734, "attribute_language", 'language="<language_value>"'),
    (735, "attribute_layout", 'layout="<layout_value>"'),
    (736, "attribute_left", 'left="<left_value>"'),
    (737, "attribute_leftmargin", 'leftmargin="<leftmargin_value>"'),
    (738, "attribute_link", 'link="<link_value>"'),
    (739, "attribute_list", 'list="<list_value>"'),
    (740, "attribute_longdesc", 'longdesc="<longdesc_value>"'),
    (741, "attribute_loop", 'loop="<loop_value>"'),
    (742, "attribute_loopend", 'loopend="<loopend_value>"'),
    (743, "attribute_loopstart", 'loopstart="<loopstart_value>"'),
    (744, "attribute_low", 'low="<low_value>"'),
    (745, "attribute_lowsrc", 'lowsrc="<lowsrc_value>"'),
    (746, "attribute_manifest", 'manifest="<manifest_value>"'),
    (747, "attribute_margin", 'margin="<margin_value>"'),
    (748, "attribute_marginheight", 'marginheight="<marginheight_value>"'),
    (749, "attribute_marginwidth", 'marginwidth="<marginwidth_value>"'),
    (750, "attribute_max", 'max="<max_value>"'),
    (751, "attribute_maxlength", 'maxlength="<maxlength_value>"'),
    (752, "attribute_mayscript", 'mayscript="<mayscript_value>"'),
    (753, "attribute_media", 'media="<media_value>"'),
    (754, "attribute_menu", 'menu="<menu_value>"'),
    (755, "attribute_method", 'method="<method_value>"'),
    (756, "attribute_min", 'min="<min_value>"'),
    (757, "attribute_minlength", 'minlength="<minlength_value>"'),
    (758, "attribute_mode", 'mode="<mode_value>"'),
    (759, "attribute_multiple", 'multiple="<multiple_value>"'),
    (760, "attribute_muted", 'muted="<muted_value>"'),
    (761, "attribute_name", 'name="<name_value>"'),
    (762, "attribute_nohref", 'nohref="<nohref_value>"'),
    (763, "attribute_nonce", 'nonce="<nonce_value>"'),
    (764, "attribute_noresize", 'noresize="<noresize_value>"'),
    (765, "attribute_noshade", 'noshade="<noshade_value>"'),
    (766, "attribute_novalidate", 'novalidate="<novalidate_value>"'),
    (767, "attribute_nowrap", 'nowrap="<nowrap_value>"'),
    (768, "attribute_open", 'open="<open_value>"'),
    (769, "attribute_optimum", 'optimum="<optimum_value>"'),
    (770, "attribute_pattern", 'pattern="<pattern_value>"'),
    (771, "attribute_ping", 'ping="<ping_value>"'),
    (772, "attribute_placeholder", 'placeholder="<placeholder_value>"'),
    (773, "attribute_playcount", 'playcount="<playcount_value>"'),
    (774, "attribute_pluginspage", 'pluginspage="<pluginspage_value>"'),
    (775, "attribute_poster", 'poster="<poster_value>"'),
    (776, "attribute_preload", 'preload="<preload_value>"'),
    (777, "attribute_profile", 'profile="<profile_value>"'),
    (778, "attribute_prompt", 'prompt="<prompt_value>"'),
    (779, "attribute_quality", 'quality="<quality_value>"'),
    (780, "attribute_radiogroup", 'radiogroup="<radiogroup_value>"'),
    (781, "attribute_readonly", 'readonly="<readonly_value>"'),
    (782, "attribute_ref", 'ref="<ref_value>"'),
    (783, "attribute_referrerpolicy", 'referrerpolicy="<referrerpolicy_value>"'),
    (784, "attribute_rel", 'rel="<rel_value>"'),
    (785, "attribute_required", 'required="<required_value>"'),
    (786, "attribute_results", 'results="<results_value>"'),
    (787, "attribute_rev", 'rev="<rev_value>"'),
    (788, "attribute_reversed", 'reversed="<reversed_value>"'),
    (789, "attribute_right", 'right="<right_value>"'),
    (790, "attribute_rightmargin", 'rightmargin="<rightmargin_value>"'),
    (791, "attribute_role", 'role="<role_value>"'),
    (792, "attribute_row", 'row="<row_value>"'),
    (793, "attribute_rows", 'rows="<rows_value>"'),
    (794, "attribute_rowspan", 'rowspan="<rowspan_value>"'),
    (795, "attribute_rules", 'rules="<rules_value>"'),
    (796, "attribute_sandbox", 'sandbox="<sandbox_value>"'),
    (797, "attribute_scheme", 'scheme="<scheme_value>"'),
    (798, "attribute_scope", 'scope="<scope_value>"'),
    (799, "attribute_scoped", 'scoped="<scoped_value>"'),
    (800, "attribute_scrollamount", 'scrollamount="<scrollamount_value>"'),
    (801, "attribute_scrolldelay", 'scrolldelay="<scrolldelay_value>"'),
    (802, "attribute_scrolling", 'scrolling="<scrolling_value>"'),
    (803, "attribute_seamless", 'seamless="<seamless_value>"'),
    (804, "attribute_seed", 'seed="<seed_value>"'),
    (805, "attribute_select", 'select="<select_value>"'),
    (806, "attribute_selected", 'selected="<selected_value>"'),
    (807, "attribute_shape", 'shape="<shape_value>"'),
    (808, "attribute_shouldfocus", 'shouldfocus="<shouldfocus_value>"'),
    (809, "attribute_size", 'size="<size_value>"'),
    (810, "attribute_sizes", 'sizes="<sizes_value>"'),
    (811, "attribute_slope", 'slope="<slope_value>"'),
    (812, "attribute_slot", 'slot="<slot_value>"'),
    (813, "attribute_span", 'span="<span_value>"'),
    (814, "attribute_spellcheck", 'spellcheck="<spellcheck_value>"'),
    (815, "attribute_src", 'src="<src_value>"'),
    (816, "attribute_srcdoc", 'srcdoc="<srcdoc_value>"'),
    (817, "attribute_srcset", 'srcset="<srcset_value>"'),
    (818, "attribute_srclang", 'srclang="<srclang_value>"'),
    (819, "attribute_standby", 'standby="<standby_value>"'),
    (820, "attribute_start", 'start="<start_value>"'),
    (821, "attribute_startoffset", 'startoffset="<startoffset_value>"'),
    (822, "attribute_startval", 'startval="<startval_value>"'),
    (823, "attribute_step", 'step="<step_value>"'),
    (824, "attribute_style", 'style="<style_value>"'),
    (825, "attribute_summary", 'summary="<summary_value>"'),
    (826, "attribute_tabindex", 'tabindex="<tabindex_value>"'),
    (827, "attribute_target", 'target="<target_value>"'),
    (828, "attribute_text", 'text="<text_value>"'),
    (829, "attribute_title", 'title="<title_value>"'),
    (830, "attribute_topmargin", 'topmargin="<topmargin_value>"'),
    (831, "attribute_translate", 'translate="<translate_value>"'),
    (832, "attribute_truespeed", 'truespeed="<truespeed_value>"'),
    (833, "attribute_type", 'type="<type_value>"'),
    (834, "attribute_usemap", 'usemap="<usemap_value>"'),
    (835, "attribute_valign", 'valign="<valign_value>"'),
    (836, "attribute_value", 'value="<value_value>"'),
    (837, "attribute_valuetype", 'valuetype="<valuetype_value>"'),
    (838, "attribute_version", 'version="<version_value>"'),
    (839, "attribute_vlink", 'vlink="<vlink_value>"'),
    (840, "attribute_vspace", 'vspace="<vspace_value>"'),
    (841, "attribute_width", 'width="<width_value>"'),
    (842, "attribute_wrap", 'wrap="<wrap_value>"'),
    (845, "attribute_onabort", 'onabort="<eventhandler>"'),
    (846, "attribute_onautocomplete", 'onautocomplete="<eventhandler>"'),
    (847, "attribute_onautocompleteerror", 'onautocompleteerror="<eventhandler>"'),
    (848, "attribute_onafterscriptexecute", 'onafterscriptexecute="<eventhandler>"'),
    (849, "attribute_onanimationend", 'onanimationend="<eventhandler>"'),
    (850, "attribute_onanimationiteration", 'onanimationiteration="<eventhandler>"'),
    (851, "attribute_onanimationstart", 'onanimationstart="<eventhandler>"'),
    (852, "attribute_onbeforecopy", 'onbeforecopy="<eventhandler>"'),
    (853, "attribute_onbeforecut", 'onbeforecut="<eventhandler>"'),
    (854, "attribute_onbeforeload", 'onbeforeload="<eventhandler>"'),
    (855, "attribute_onbeforepaste", 'onbeforepaste="<eventhandler>"'),
    (856, "attribute_onbeforescriptexecute", 'onbeforescriptexecute="<eventhandler>"'),
    (857, "attribute_onbeforeunload", 'onbeforeunload="<eventhandler>"'),
    (858, "attribute_onbegin", 'onbegin="<eventhandler>"'),
    (859, "attribute_onblur", 'onblur="<eventhandler>"'),
    (860, "attribute_oncanplay", 'oncanplay="<eventhandler>"'),
    (861, "attribute_oncanplaythrough", 'oncanplaythrough="<eventhandler>"'),
    (862, "attribute_onchange", 'onchange="<eventhandler>"'),
    (863, "attribute_onclick", 'onclick="<eventhandler>"'),
    (864, "attribute_oncontextmenu", 'oncontextmenu="<eventhandler>"'),
    (865, "attribute_oncopy", 'oncopy="<eventhandler>"'),
    (866, "attribute_oncut", 'oncut="<eventhandler>"'),
    (867, "attribute_ondblclick", 'ondblclick="<eventhandler>"'),
    (868, "attribute_ondrag", 'ondrag="<eventhandler>"'),
    (869, "attribute_ondragend", 'ondragend="<eventhandler>"'),
    (870, "attribute_ondragenter", 'ondragenter="<eventhandler>"'),
    (871, "attribute_ondragleave", 'ondragleave="<eventhandler>"'),
    (872, "attribute_ondragover", 'ondragover="<eventhandler>"'),
    (873, "attribute_ondragstart", 'ondragstart="<eventhandler>"'),
    (874, "attribute_ondrop", 'ondrop="<eventhandler>"'),
    (875, "attribute_ondurationchange", 'ondurationchange="<eventhandler>"'),
    (876, "attribute_onend", 'onend="<eventhandler>"'),
    (877, "attribute_onemptied", 'onemptied="<eventhandler>"'),
    (878, "attribute_onended", 'onended="<eventhandler>"'),
    (879, "attribute_onerror", 'onerror="<eventhandler>"'),
    (880, "attribute_onfocus", 'onfocus="<eventhandler>"'),
    (881, "attribute_onfocusin", 'onfocusin="<eventhandler>"'),
    (882, "attribute_onfocusout", 'onfocusout="<eventhandler>"'),
    (883, "attribute_onhashchange", 'onhashchange="<eventhandler>"'),
    (884, "attribute_oninput", 'oninput="<eventhandler>"'),
    (885, "attribute_oninvalid", 'oninvalid="<eventhandler>"'),
    (886, "attribute_onkeydown", 'onkeydown="<eventhandler>"'),
    (887, "attribute_onkeypress", 'onkeypress="<eventhandler>"'),
    (888, "attribute_onkeyup", 'onkeyup="<eventhandler>"'),
    (889, "attribute_onload", 'onload="<eventhandler>"'),
    (890, "attribute_onloadeddata", 'onloadeddata="<eventhandler>"'),
    (891, "attribute_onloadedmetadata", 'onloadedmetadata="<eventhandler>"'),
    (892, "attribute_onloadstart", 'onloadstart="<eventhandler>"'),
    (893, "attribute_onmessage", 'onmessage="<eventhandler>"'),
    (894, "attribute_onmousedown", 'onmousedown="<eventhandler>"'),
    (895, "attribute_onmouseenter", 'onmouseenter="<eventhandler>"'),
    (896, "attribute_onmouseleave", 'onmouseleave="<eventhandler>"'),
    (897, "attribute_onmousemove", 'onmousemove="<eventhandler>"'),
    (898, "attribute_onmouseout", 'onmouseout="<eventhandler>"'),
    (899, "attribute_onmouseover", 'onmouseover="<eventhandler>"'),
    (900, "attribute_onmouseup", 'onmouseup="<eventhandler>"'),
    (901, "attribute_onmousewheel", 'onmousewheel="<eventhandler>"'),
    (902, "attribute_onoffline", 'onoffline="<eventhandler>"'),
    (903, "attribute_ononline", 'ononline="<eventhandler>"'),
    (904, "attribute_onorientationchange", 'onorientationchange="<eventhandler>"'),
    (905, "attribute_onpagehide", 'onpagehide="<eventhandler>"'),
    (906, "attribute_onpageshow", 'onpageshow="<eventhandler>"'),
    (907, "attribute_onpaste", 'onpaste="<eventhandler>"'),
    (908, "attribute_onpause", 'onpause="<eventhandler>"'),
    (909, "attribute_onplay", 'onplay="<eventhandler>"'),
    (910, "attribute_onplaying", 'onplaying="<eventhandler>"'),
    (911, "attribute_onpopstate", 'onpopstate="<eventhandler>"'),
    (912, "attribute_onprogress", 'onprogress="<eventhandler>"'),
    (913, "attribute_onratechange", 'onratechange="<eventhandler>"'),
    (914, "attribute_onreset", 'onreset="<eventhandler>"'),
    (915, "attribute_onresize", 'onresize="<eventhandler>"'),
    (916, "attribute_onscroll", 'onscroll="<eventhandler>"'),
    (917, "attribute_onsearch", 'onsearch="<eventhandler>"'),
    (918, "attribute_onseeked", 'onseeked="<eventhandler>"'),
    (919, "attribute_onseeking", 'onseeking="<eventhandler>"'),
    (920, "attribute_onselect", 'onselect="<eventhandler>"'),
    (921, "attribute_onselectionchange", 'onselectionchange="<eventhandler>"'),
    (922, "attribute_onselectstart", 'onselectstart="<eventhandler>"'),
    (923, "attribute_onstalled", 'onstalled="<eventhandler>"'),
    (924, "attribute_onstorage", 'onstorage="<eventhandler>"'),
    (925, "attribute_onsubmit", 'onsubmit="<eventhandler>"'),
    (926, "attribute_onsuspend", 'onsuspend="<eventhandler>"'),
    (927, "attribute_ontimeupdate", 'ontimeupdate="<eventhandler>"'),
    (928, "attribute_ontoggle", 'ontoggle="<eventhandler>"'),
    (929, "attribute_ontouchcancel", 'ontouchcancel="<eventhandler>"'),
    (930, "attribute_ontouchend", 'ontouchend="<eventhandler>"'),
    (931, "attribute_ontouchmove", 'ontouchmove="<eventhandler>"'),
    (932, "attribute_ontouchstart", 'ontouchstart="<eventhandler>"'),
    (933, "attribute_ontransitionend", 'ontransitionend="<eventhandler>"'),
    (934, "attribute_onunload", 'onunload="<eventhandler>"'),
    (935, "attribute_onvolumechange", 'onvolumechange="<eventhandler>"'),
    (936, "attribute_onwaiting", 'onwaiting="<eventhandler>"'),
    (937, "attribute_onwebkitanimationend", 'onwebkitanimationend="<eventhandler>"'),
    (938, "attribute_onwebkitanimationiteration", 'onwebkitanimationiteration="<eventhandler>"'),
    (939, "attribute_onwebkitanimationstart", 'onwebkitanimationstart="<eventhandler>"'),
    (940, "attribute_onwebkitfullscreenchange", 'onwebkitfullscreenchange="<eventhandler>"'),
    (941, "attribute_onwebkitfullscreenerror", 'onwebkitfullscreenerror="<eventhandler>"'),
    (942, "attribute_onwebkitkeyadded", 'onwebkitkeyadded="<eventhandler>"'),
    (943, "attribute_onwebkitkeyerror", 'onwebkitkeyerror="<eventhandler>"'),
    (944, "attribute_onwebkitkeymessage", 'onwebkitkeymessage="<eventhandler>"'),
    (945, "attribute_onwebkitneedkey", 'onwebkitneedkey="<eventhandler>"'),
    (946, "attribute_onwebkitsourceclose", 'onwebkitsourceclose="<eventhandler>"'),
    (947, "attribute_onwebkitsourceended", 'onwebkitsourceended="<eventhandler>"'),
    (948, "attribute_onwebkitsourceopen", 'onwebkitsourceopen="<eventhandler>"'),
    (949, "attribute_onwebkitspeechchange", 'onwebkitspeechchange="<eventhandler>"'),
    (950, "attribute_onwebkittransitionend", 'onwebkittransitionend="<eventhandler>"'),
    (951, "attribute_onwheel", 'onwheel="<eventhandler>"'),
    (953, "attributestring", "<htmlsafestring min=32 max=126>"),
    (954, "attributechar", "<char min=32 max=126>"),
    (959, "HTMLAnchorElement", "<lt>a <a_attributes> <attributes><gt><innerelements><lt>/a<gt>"),
    (960, "otherelement", "<lt>abbr <abbr_attributes> <attributes><gt><innerelements><lt>/abbr<gt>"),
    (961, "otherelement", ("<lt>acronym <acronym_attributes> <attributes><gt><innerelements><lt>/acronym<gt>")),
    (962, "otherelement", ("<lt>address <address_attributes> <attributes><gt><innerelements><lt>/address<gt>")),
    (
        963,
        "HTMLAppletElement",
        ("<lt>applet <applet_attributes> <attributes><gt><htmlsafestring min=32 max=126><lt>/applet<gt>"),
    ),
    (964, "HTMLAreaElement", "<lt>area <area_attributes> <attributes><gt><lt>/area<gt>"),
    (965, "otherelement", ("<lt>article <article_attributes> <attributes><gt><innerelements><lt>/article<gt>")),
    (966, "otherelement", "<lt>aside <aside_attributes> <attributes><gt><innerelements><lt>/aside<gt>"),
    (967, "HTMLAudioElement", "<lt>audio <audio_attributes> <attributes><gt><mediachildren><lt>/audio<gt>"),
    (968, "otherelement", "<lt>b <b_attributes> <attributes><gt><innerelements><lt>/b<gt>"),
    (969, "HTMLBaseElement", "<lt>base <base_attributes> <attributes><gt><lt>/base<gt>"),
    (970, "HTMLBaseFontElement", "<lt>basefont <basefont_attributes> <attributes><gt><lt>/basefont<gt>"),
    (971, "otherelement", "<lt>bdi <bdi_attributes> <attributes><gt><innerelements><lt>/bdi<gt>"),
    (972, "otherelement", "<lt>bdo <bdo_attributes> <attributes><gt><innerelements><lt>/bdo<gt>"),
    (973, "otherelement", "<lt>bgsound <attributes><gt><innerelements><lt>/bgsound<gt>"),
    (974, "otherelement", "<lt>big <big_attributes> <attributes><gt><innerelements><lt>/big<gt>"),
    (975, "otherelement", "<lt>blink <attributes><gt><innerelements><lt>/blink<gt>"),
    (
        976,
        "otherelement",
        ("<lt>blockquote <blockquote_attributes> <attributes><gt><innerelements><lt>/blockquote<gt>"),
    ),
    (977, "HTMLBRElement", "<lt>br <br_attributes> <attributes><gt><lt>/br<gt>"),
    (978, "HTMLButtonElement", "<lt>button <button_attributes> <attributes><gt><innerelements><lt>/button<gt>"),
    (979, "HTMLCanvasElement", "<lt>canvas <canvas_attributes> <attributes><gt><innerelements><lt>/canvas<gt>"),
    (
        980,
        "HTMLTableCaptionElement",
        ("<lt>caption <caption_attributes> <attributes><gt><innerelements><lt>/caption<gt>"),
    ),
    (981, "otherelement", "<lt>center <center_attributes> <attributes><gt><innerelements><lt>/center<gt>"),
    (982, "otherelement", "<lt>cite <cite_attributes> <attributes><gt><innerelements><lt>/cite<gt>"),
    (983, "otherelement", "<lt>code <code_attributes> <attributes><gt><innerelements><lt>/code<gt>"),
    (984, "HTMLTableColElement", "<lt>col <col_attributes> <attributes><gt><innerelements><lt>/col<gt>"),
    (985, "colgroupelement", ("<lt>colgroup <colgroup_attributes> <attributes><gt><colelements><lt>/colgroup<gt>")),
    (986, "otherelement", ("<lt>command <command_attributes> <attributes><gt><innerelements><lt>/command<gt>")),
    (987, "HTMLContentElement", ("<lt>content <content_attributes> <attributes><gt><innerelements><lt>/content<gt>")),
    (988, "HTMLDataElement", "<lt>data <data_attributes> <attributes><gt><innerelements><lt>/data<gt>"),
    (
        989,
        "HTMLDataListElement",
        ("<lt>datalist <datalist_attributes> <attributes><gt><optionelements><lt>/datalist<gt>"),
    ),
    (990, "ddelement", "<lt>dd <dd_attributes> <attributes><gt><innerelements><lt>/dd<gt>"),
    (991, "HTMLModElement", "<lt>del <del_attributes> <attributes><gt><innerelements><lt>/del<gt>"),
    (992, "HTMLDetailsElement", ("<lt>details <details_attributes> <attributes><gt><detailchildren><lt>/details<gt>")),
    (993, "otherelement", "<lt>dfn <dfn_attributes> <attributes><gt><innerelements><lt>/dfn<gt>"),
    (994, "HTMLDialogElement", "<lt>dialog <dialog_attributes> <attributes><gt><innerelements><lt>/dialog<gt>"),
    (995, "HTMLDirectoryElement", "<lt>dir <dir_attributes> <attributes><gt><lielements><lt>/dir<gt>"),
    (996, "HTMLDivElement", "<lt>div <div_attributes> <attributes><gt><innerelements><lt>/div<gt>"),
    (997, "HTMLDListElement", "<lt>dl <dl_attributes> <attributes><gt><dlchildren><lt>/dl<gt>"),
    (998, "dtelement", "<lt>dt <dt_attributes> <attributes><gt><innerelements><lt>/dt<gt>"),
    (999, "otherelement", "<lt>em <em_attributes> <attributes><gt><innerelements><lt>/em<gt>"),
    (1000, "HTMLEmbedElement", "<lt>embed <embed_attributes> <attributes><gt><innerelements><lt>/embed<gt>"),
    (
        1001,
        "HTMLFieldSetElement",
        ("<lt>fieldset <fieldset_attributes> <attributes><gt><formchildren><lt>/fieldset<gt>"),
    ),
    (1002, "otherelement", "<lt>figcaption <attributes><gt><innerelements><lt>/figcaption<gt>"),
    (1003, "otherelement", "<lt>figure <figure_attributes> <attributes><gt><innerelements><lt>/figure<gt>"),
    (1004, "HTMLFontElement", "<lt>font <font_attributes> <attributes><gt><innerelements><lt>/font<gt>"),
    (1005, "otherelement", "<lt>footer <footer_attributes> <attributes><gt><innerelements><lt>/footer<gt>"),
    (1006, "HTMLFormElement", "<lt>form <form_attributes> <attributes><gt><formchildren><lt>/form<gt>"),
    (1007, "HTMLFrameElement", "<lt>frame <frame_attributes> <attributes><gt><innerelements><lt>/frame<gt>"),
    (1008, "HTMLFrameSetElement", "<lt>frameset <frameset_attributes> <attributes><gt><frames><lt>/frameset<gt>"),
    (1009, "HTMLHeadingElement", "<lt>h1 <h1_attributes> <attributes><gt><innerelements><lt>/h1<gt>"),
    (1010, "HTMLHeadingElement", "<lt>h2 <h2_attributes> <attributes><gt><innerelements><lt>/h2<gt>"),
    (1011, "HTMLHeadingElement", "<lt>h3 <h3_attributes> <attributes><gt><innerelements><lt>/h3<gt>"),
    (1012, "HTMLHeadingElement", "<lt>h4 <h4_attributes> <attributes><gt><innerelements><lt>/h4<gt>"),
    (1013, "HTMLHeadingElement", "<lt>h5 <h5_attributes> <attributes><gt><innerelements><lt>/h5<gt>"),
    (1014, "HTMLHeadingElement", "<lt>h6 <h6_attributes> <attributes><gt><innerelements><lt>/h6<gt>"),
    (1015, "otherelement", "<lt>header <header_attributes> <attributes><gt><innerelements><lt>/header<gt>"),
    (1016, "otherelement", "<lt>hgroup <hgroup_attributes> <attributes><gt><innerelements><lt>/hgroup<gt>"),
    (1017, "HTMLHRElement", "<lt>hr <hr_attributes> <attributes><gt><innerelements><lt>/hr<gt>"),
    (1018, "otherelement", "<lt>i <i_attributes> <attributes><gt><innerelements><lt>/i<gt>"),
    (
        1019,
        "HTMLIFrameElement",
        ("<lt>iframe <iframe_attributes> <attributes><gt><htmlsafestring min=32 max=126><lt>/iframe<gt>"),
    ),
    (1020, "HTMLImageElement", "<lt>image <image_attributes> <attributes><gt><lt>/image<gt>"),
    (1021, "HTMLImageElement", "<lt>img <img_attributes> <attributes><gt><lt>/img<gt>"),
    (1022, "HTMLInputElement", "<lt>input <input_attributes> type=<inputtype> <attributes><gt>"),
    (1023, "HTMLModElement", "<lt>ins <ins_attributes> <attributes><gt><innerelements><lt>/ins<gt>"),
    (1024, "otherelement", "<lt>isindex <attributes><gt><innerelements><lt>/isindex<gt>"),
    (1025, "otherelement", "<lt>kbd <kbd_attributes> <attributes><gt><innerelements><lt>/kbd<gt>"),
    (1026, "HTMLKeygenElement", "<lt>keygen <keygen_attributes> <attributes><gt>"),
    (1027, "HTMLLabelElement", "<lt>label <label_attributes> <attributes><gt><innerelements><lt>/label<gt>"),
    (1028, "otherelement", "<lt>layer <attributes><gt><innerelements><lt>/layer<gt>"),
    (1029, "HTMLLegendElement", "<lt>legend <legend_attributes> <attributes><gt><innerelements><lt>/legend<gt>"),
    (1030, "HTMLLIElement", "<lt>li <li_attributes> <attributes><gt><innerelements><lt>/li<gt>"),
    (1031, "HTMLLinkElement", "<lt>link <link_attributes> <attributes><gt><innerelements><lt>/link<gt>"),
    (1032, "otherelement", ("<lt>listing <listing_attributes> <attributes><gt><innerelements><lt>/listing<gt>")),
    (1033, "otherelement", "<lt>main <main_attributes> <attributes><gt><innerelements><lt>/main<gt>"),
    (1034, "HTMLMapElement", "<lt>map <map_attributes> <attributes><gt><areaelements><lt>/map<gt>"),
    (1035, "otherelement", "<lt>mark <mark_attributes> <attributes><gt><innerelements><lt>/mark<gt>"),
    (1036, "HTMLMarqueeElement", ("<lt>marquee <marquee_attributes> <attributes><gt><innerelements><lt>/marquee<gt>")),
    (1037, "HTMLMenuElement", "<lt>menu <menu_attributes> <attributes><gt><menuchildren><lt>/menu<gt>"),
    (1038, "menuitemelement", ("<lt>menuitem <menuitem_attributes> <attributes><gt><innerelements><lt>/menuitem<gt>")),
    (1039, "HTMLMetaElement", "<lt>meta <meta_attributes> <attributes><gt><lt>/meta<gt>"),
    (1040, "HTMLMeterElement", "<lt>meter <meter_attributes> <attributes><gt><innerelements><lt>/meter<gt>"),
    (1041, "otherelement", "<lt>nav <nav_attributes> <attributes><gt><innerelements><lt>/nav<gt>"),
    (1042, "otherelement", "<lt>nobr <nobr_attributes> <attributes><gt><innerelements><lt>/nobr<gt>"),
    (1043, "otherelement", "<lt>noembed <attributes><gt><innerelements><lt>/noembed<gt>"),
    (
        1044,
        "otherelement",
        ("<lt>noframes <noframes_attributes> <attributes><gt><htmlsafestring min=32 max=126><lt>/noframes<gt>"),
    ),
    (1045, "otherelement", "<lt>nolayer <attributes><gt><innerelements><lt>/nolayer<gt>"),
    (
        1046,
        "otherelement",
        ("<lt>noscript <noscript_attributes> <attributes><gt><htmlsafestring min=32 max=126><lt>/noscript<gt>"),
    ),
    (1047, "HTMLObjectElement", "<lt>object <object_attributes> <attributes><gt><paramelements><lt>/object<gt>"),
    (1048, "HTMLOListElement", "<lt>ol <ol_attributes> <attributes><gt><lielements><lt>/ol<gt>"),
    (
        1049,
        "HTMLOptGroupElement",
        ("<lt>optgroup <optgroup_attributes> <attributes><gt><optionelements><lt>/optgroup<gt>"),
    ),
    (1050, "HTMLOptionElement", "<lt>option <option_attributes> <attributes><gt><innerelements><lt>/option<gt>"),
    (1051, "HTMLOutputElement", "<lt>output <output_attributes> <attributes><gt><innerelements><lt>/output<gt>"),
    (1052, "HTMLParagraphElement", "<lt>p <p_attributes> <attributes><gt><innerelements><lt>/p<gt>"),
    (1053, "HTMLParamElement", "<lt>param <param_attributes> <attributes><gt><lt>/param<gt>"),
    (1055, "HTMLPreElement", "<lt>pre <pre_attributes> <attributes><gt><innerelements><lt>/pre<gt>"),
    (
        1056,
        "HTMLProgressElement",
        ("<lt>progress <progress_attributes> <attributes><gt><innerelements><lt>/progress<gt>"),
    ),
    (1057, "HTMLQuoteElement", "<lt>q <q_attributes> <attributes><gt><innerelements><lt>/q<gt>"),
    (1058, "otherelement", "<lt>rp <rp_attributes> <attributes><gt><innerelements><lt>/rp<gt>"),
    (1059, "otherelement", "<lt>rt <rt_attributes> <attributes><gt><innerelements><lt>/rt<gt>"),
    (1060, "otherelement", "<lt>ruby <ruby_attributes> <attributes><gt><innerelements><lt>/ruby<gt>"),
    (1061, "otherelement", "<lt>s <s_attributes> <attributes><gt><innerelements><lt>/s<gt>"),
    (1062, "otherelement", "<lt>samp <samp_attributes> <attributes><gt><innerelements><lt>/samp<gt>"),
    (1064, "otherelement", ("<lt>section <section_attributes> <attributes><gt><innerelements><lt>/section<gt>")),
    (1065, "HTMLSelectElement", "<lt>select <select_attributes> <attributes><gt><selectchildren><lt>/select<gt>"),
    (1066, "HTMLShadowElement", "<lt>shadow <attributes><gt><innerelements><lt>/shadow<gt>"),
    (1067, "otherelement", "<lt>small <small_attributes> <attributes><gt><innerelements><lt>/small<gt>"),
    (1068, "HTMLSourceElement", "<lt>source <source_attributes> <attributes><gt><innerelements><lt>/source<gt>"),
    (1069, "otherelement", "<lt>spacer <spacer_attributes> <attributes><gt><innerelements><lt>/spacer<gt>"),
    (1070, "HTMLSpanElement", "<lt>span <span_attributes> <attributes><gt><innerelements><lt>/span<gt>"),
    (1071, "otherelement", "<lt>strike <strike_attributes> <attributes><gt><innerelements><lt>/strike<gt>"),
    (1072, "otherelement", "<lt>strong <strong_attributes> <attributes><gt><innerelements><lt>/strong<gt>"),
    (
        1073,
        "HTMLStyleElement",
        ("<lt>style <style_attributes> <attributes><gt><htmlsafestring min=32 max=126><lt>/style<gt>"),
    ),
    (1074, "otherelement", "<lt>sub <sub_attributes> <attributes><gt><innerelements><lt>/sub<gt>"),
    (1075, "summaryelement", ("<lt>summary <summary_attributes> <attributes><gt><innerelements><lt>/summary<gt>")),
    (1076, "otherelement", "<lt>sup <sup_attributes> <attributes><gt><innerelements><lt>/sup<gt>"),
    (1077, "HTMLTableElement", "<lt>table <table_attributes> <attributes><gt><tablechildren><lt>/table<gt>"),
    (1078, "HTMLTableSectionElement", "<lt>tbody <tbody_attributes> <attributes><gt><trelements><lt>/tbody<gt>"),
    (1079, "otherelement", "<lt>td <td_attributes> <attributes><gt><innerelements><lt>/td<gt>"),
    (
        1080,
        "HTMLTemplateElement",
        ("<lt>template <template_attributes> <attributes><gt><innerelements><lt>/template<gt>"),
    ),
    (
        1081,
        "HTMLTextAreaElement",
        ("<lt>textarea <textarea_attributes> <attributes><gt><htmlsafestring min=32 max=126><lt>/textarea<gt>"),
    ),
    (1082, "HTMLTableSectionElement", "<lt>tfoot <tfoot_attributes> <attributes><gt><trelements><lt>/tfoot<gt>"),
    (1083, "HTMLTableCellElement", "<lt>th <th_attributes> <attributes><gt><innerelements><lt>/th<gt>"),
    (1084, "HTMLTableSectionElement", "<lt>thead <thead_attributes> <attributes><gt><trelements><lt>/thead<gt>"),
    (1085, "HTMLTimeElement", "<lt>time <time_attributes> <attributes><gt><innerelements><lt>/time<gt>"),
    (
        1086,
        "HTMLTitleElement",
        ("<lt>title <title_attributes> <attributes><gt><htmlsafestring min=32 max=126><lt>/title<gt>"),
    ),
    (1087, "HTMLTableRowElement", "<lt>tr <tr_attributes> <attributes><gt><thelements><lt>/tr<gt>"),
    (1088, "HTMLTrackElement", "<lt>track <track_attributes> <attributes><gt><innerelements><lt>/track<gt>"),
    (1089, "otherelement", "<lt>tt <tt_attributes> <attributes><gt><innerelements><lt>/tt<gt>"),
    (1090, "otherelement", "<lt>u <u_attributes> <attributes><gt><innerelements><lt>/u<gt>"),
    (1091, "HTMLUListElement", "<lt>ul <ul_attributes> <attributes><gt><lielements><lt>/ul<gt>"),
    (1092, "otherelement", "<lt>var <var_attributes> <attributes><gt><innerelements><lt>/var<gt>"),
    (1093, "HTMLVideoElement", "<lt>video <video_attributes> <attributes><gt><mediachildren><lt>/video<gt>"),
    (1094, "otherelement", "<lt>wbr <wbr_attributes> <attributes><gt><innerelements><lt>/wbr<gt>"),
    (1095, "otherelement", ("<lt>xmp <xmp_attributes> <attributes><gt><htmlsafestring min=32 max=126><lt>/xmp<gt>")),
)

ATTRIBUTE_VALUE_RULES: Final[tuple[TableRule, ...]] = (
    (15, "charset", "<attributestring>"),
    (16, "charset", "UTF-7"),
    (17, "charset", "UTF-8"),
    (18, "charset", "UTF-16"),
    (19, "charset", "UTF-32"),
    (20, "charset", "EUC-JP"),
    (21, "charset", "ISO-2022-JP"),
    (22, "charset", "iso-8859-1"),
    (23, "charset", "Big5"),
    (24, "charset", "Shift_JIS"),
    (25, "charset", "UTF-8"),
    (26, "charset", "US-ASCII"),
    (28, "onoff", "on"),
    (29, "onoff", "off"),
    (31, "abbr_value", "<attributestring>"),
    (33, "accept_value", "<attributestring>"),
    (34, "accept_value", "audio/*"),
    (35, "accept_value", "video/*"),
    (36, "accept_value", "image/*"),
    (38, "accept-charset_value", "<charset>"),
    (40, "accepts-touch_value", "synthetic"),
    (41, "accepts-touch_value", "raw"),
    (43, "accesskey_value", "<attributechar>"),
    (45, "accumulate_value", "sum"),
    (46, "accumulate_value", "none"),
    (50, "additive_value", "sum"),
    (51, "additive_value", "replace"),
    (53, "align_value", ""),
    (54, "align_value", "char"),
    (55, "align_value", "LEFT"),
    (56, "align_value", "right"),
    (57, "align_value", "center"),
    (58, "align_value", "bottom"),
    (59, "align_value", "foobar"),
    (60, "align_value", "top"),
    (61, "align_value", "BOTTOM"),
    (62, "align_value", "MIDDLE"),
    (63, "align_value", "ABSMIDDLE"),
    (64, "align_value", "Right"),
    (65, "align_value", "middle"),
    (66, "align_value", "bad"),
    (67, "align_value", "RIGHT"),
    (68, "align_value", "Left"),
    (69, "align_value", "absmiddle"),
    (70, "align_value", "left"),
    (71, "align_value", "CENTER"),
    (72, "align_value", "justify"),
    (74, "alink_value", "<color>"),
    (76, "allowfullscreen_value", "<boolean>"),
    (78, "alt_value", "<attributestring>"),
    (80, "archive_value", "<attributestring>"),
    (82, "aria-activedescendant_value", "<elementid>"),
    (84, "aria-autocomplete_value", "list"),
    (86, "aria-atomic_value", "<boolean>"),
    (88, "aria-busy_value", "<boolean>"),
    (90, "aria-checked_value", "mixed"),
    (91, "aria-checked_value", "<boolean>"),
    (93, "aria-controls_value", "<elementid>"),
    (95, "aria-describedby_value", "<elementid>"),
    (97, "aria-disabled_value", "<boolean>"),
    (99, "aria-dropeffect_value", "copy"),
    (100, "aria-dropeffect_value", "move"),
    (101, "aria-dropeffect_value", "link"),
    (102, "aria-dropeffect_value", "popup"),
    (103, "aria-dropeffect_value", "execute"),
    (104, "aria-dropeffect_value", "none"),
    (106, "aria-expanded_value", "<boolean>"),
    (108, "aria-flowto_value", "<elementid>"),
    (110, "aria-grabbed_value", "<boolean>"),
    (112, "aria-haspopup_value", "<boolean>"),
    (114, "aria-help_value", "<elementid>"),
    (116, "aria-hidden_value", "<boolean>"),
    (118, "aria-invalid_value", "<boolean>"),
    (120, "aria-label_value", "<attributestring>"),
    (122, "aria-labeledby_value", "<elementid>"),
    (124, "aria-labelledby_value", "<elementid>"),
    (126, "aria-level_value", "<fuzzint>"),
    (128, "aria-live_value", "off"),
    (129, "aria-live_value", "polite"),
    (130, "aria-live_value", "assertive"),
    (132, "aria-multiline_value", "<boolean>"),
    (134, "aria-multiselectable_value", "<boolean>"),
    (136, "aria-name_value", "<attributestring>"),
    (138, "aria-orientation_value", "horizontal"),
    (139, "aria-orientation_value", "vertical"),
    (141, "aria-owns_value", "<elementid>"),
    (143, "aria-posinset_value", "<fuzzint>"),
    (145, "aria-pressed_value", "<boolean>"),
    (147, "aria-readonly_value", "<boolean>"),
    (149, "aria-relevant_value", "additions"),
    (150, "aria-relevant_value", "removals"),
    (151, "aria-relevant_value", "text"),
    (152, "aria-relevant_value", "all"),
    (154, "aria-required_value", "<boolean>"),
    (156, "aria-selected_value", "<boolean>"),
    (158, "aria-setsize_value", "<fuzzint>"),
    (160, "aria-sort_value", "ascending"),
    (161, "aria-sort_value", "descending"),
    (163, "aria-valuemax_value", "<fuzzint>"),
    (165, "aria-valuemin_value", "<fuzzint>"),
    (167, "aria-valuenow_value", "<fuzzint>"),
    (169, "aria-valuetext_value", "<attributestring>"),
    (171, "as_value", "style"),
    (172, "as_value", "audio"),
    (173, "as_value", "script"),
    (174, "as_value", "track"),
    (175, "as_value", "media"),
    (176, "as_value", "image"),
    (177, "as_value", "video"),
    (178, "as_value", "font"),
    (180, "async_value", "<boolean>"),
    (182, "autocomplete_value", "<onoff>"),
    (184, "autofocus_value", "autofocus"),
    (186, "autoload_value", "autoload"),
    (188, "autoplay_value", "autoplay"),
    (190, "axis_value", "<attributestring>"),
    (192, "azimuth_value", "<fuzzint>"),
    (194, "background_value", "<attributestring>"),
    (196, "background-color_value", "<color>"),
    (198, "basefrequency_value", "<float>"),
    (200, "baseprofile_value", "full"),
    (201, "baseprofile_value", "tiny"),
    (203, "behavior_value", "slide"),
    (204, "behavior_value", "alternate"),
    (205, "behavior_value", "scroll"),
    (207, "bgcolor_value", "<color>"),
    (209, "bgproperties_value", "fixed"),
    (211, "border_value", "0"),
    (212, "border_value", "1"),
    (213, "border_value", "<fuzzint>px"),
    (215, "bordercolor_value", "<color>"),
    (217, "buffered-rendering_value", "dynamic"),
    (218, "buffered-rendering_value", "static"),
    (220, "can-process-drag_value", "<boolean>"),
    (222, "case_value", "second"),
    (223, "case_value", "first"),
    (225, "capture_value", "capture"),
    (227, "cellpadding_value", "<fuzzint>"),
    (229, "cellspacing_value", "<fuzzint>"),
    (231, "challenge_value", "<attributestring>"),
    (233, "char_value", "<attributechar>"),
    (235, "charoff_value", "<fuzzint>"),
    (237, "charset_value", "<charset>"),
    (239, "checked_value", "checked"),
    (241, "cite_value", "<attributestring>"),
    (243, "class_value", "<class>"),
    (245, "classid_value", "<attributestring>"),
    (247, "clear_value", "none"),
    (248, "clear_value", "left"),
    (249, "clear_value", "ALL"),
    (250, "clear_value", "all"),
    (252, "code_value", "<attributestring>"),
    (254, "codebase_value", "<attributestring>"),
    (256, "codetype_value", "image/gif"),
    (258, "color_value", "<color>"),
    (260, "cols_value", "<fuzzint>"),
    (261, "cols_value", "<fuzzint>,<fuzzint>"),
    (263, "colspan_value", "<fuzzint>"),
    (265, "compact_value", "compact"),
    (267, "content_value", "<attributestring>"),
    (269, "contenteditable_value", "false"),
    (270, "contenteditable_value", "inherit"),
    (271, "contenteditable_value", "plaintext-only"),
    (272, "contenteditable_value", "true"),
    (274, "contextmenu_value", "<elementid>"),
    (276, "controls_value", "controls"),
    (278, "coords_value", "<fuzzint>, <fuzzint>, <fuzzint>, <fuzzint>"),
    (280, "crossorigin_value", "crossorigin"),
    (282, "data_value", "<attributestring>"),
    (284, "datetime_value", "January 2, 2002"),
    (285, "datetime_value", "January 1, 2002"),
    (286, "datetime_value", "2000-02-01T03:04:05Z"),
    (288, "declare_value", "declare"),
    (290, "default_value", "<attributestring>"),
    (292, "defer_value", "defer"),
    (294, "desc_value", "<attributestring>"),
    (296, "description_value", "<attributestring>"),
    (298, "dir_value", "LTR"),
    (299, "dir_value", "auto"),
    (300, "dir_value", "ltr"),
    (301, "dir_value", "RTL"),
    (302, "dir_value", "rtl"),
    (304, "direction_value", "ltr"),
    (305, "direction_value", "rtl"),
    (307, "dirname_value", "<attributestring>"),
    (309, "disabled_value", "disabled"),
    (311, "display_value", "none"),
    (312, "display_value", "block"),
    (314, "disposition_value", "attachment"),
    (316, "download_value", "<attributestring>"),
    (318, "draggable_value", "<boolean>"),
    (320, "encoding_value", "TEXT/HTML"),
    (321, "encoding_value", "text/html"),
    (322, "encoding_value", "TeX"),
    (323, "encoding_value", "MathML-Presentation"),
    (324, "encoding_value", "foo"),
    (325, "encoding_value", "application/xhtml+xml"),
    (327, "enctype_value", "multipart/form-data"),
    (328, "enctype_value", "text/plain"),
    (329, "enctype_value", "application/x-www-form-urlencoded"),
    (331, "expanded_value", "<boolean>"),
    (333, "face_value", "Arial, helvetica"),
    (334, "face_value", "Arial"),
    (335, "face_value", "ms sans serif, helvetica"),
    (336, "face_value", "Helvetica"),
    (337, "face_value", "Verdana"),
    (338, "face_value", "Arial,Helvetica"),
    (339, "face_value", "fantasy"),
    (340, "face_value", "arial,helvetica,sans-serif"),
    (341, "face_value", "Helvetica, Arial"),
    (342, "face_value", "sans-serif, arial"),
    (343, "face_value", "Arial,Helvetica,sans-serif"),
    (344, "face_value", "arial"),
    (345, "face_value", "Courier"),
    (346, "face_value", "helvetica"),
    (347, "face_value", "verdana"),
    (348, "face_value", "Arial,helvetica"),
    (349, "face_value", "Times New Roman"),
    (350, "face_value", " helvetica"),
    (351, "face_value", "monospace"),
    (352, "face_value", "Arial, sans-serif"),
    (353, "face_value", "Lucida Grande"),
    (354, "face_value", "ARIAL,HELVETICA"),
    (355, "face_value", "serif"),
    (356, "face_value", "Monaco"),
    (357, "face_value", "arial,helvetica"),
    (358, "face_value", "Verdana, Arial"),
    (359, "face_value", "'courier new', monospace"),
    (360, "face_value", "arial, sans-serif"),
    (361, "face_value", "sans-serif"),
    (362, "face_value", "Comic Sans MS"),
    (363, "face_value", "n8 n26"),
    (364, "face_value", "ariel,helvetica"),
    (365, "face_value", "cursive"),
    (366, "face_value", "Times"),
    (368, "focus_value", "<boolean>"),
    (370, "focused_value", "<boolean>"),
    (372, "for_value", "<elementid>"),
    (374, "form_value", "<elementid>"),
    (376, "formaction_value", "<eventhandler>"),
    (378, "formenctype_value", "text/plain"),
    (380, "formmethod_value", "post"),
    (381, "formmethod_value", "get"),
    (383, "formnovalidate_value", "formnovalidate"),
    (385, "formtarget_value", "<elementid>"),
    (387, "frame_value", "box"),
    (388, "frame_value", "BOX"),
    (389, "frame_value", "vsides"),
    (390, "frame_value", "hsides"),
    (391, "frame_value", "void"),
    (392, "frame_value", "below"),
    (393, "frame_value", "lhs"),
    (394, "frame_value", "above"),
    (395, "frame_value", "border"),
    (396, "frame_value", "rhs"),
    (398, "frameborder_value", "1"),
    (399, "frameborder_value", "0"),
    (401, "framemargin_value", "0"),
    (402, "framemargin_value", "1"),
    (404, "framespacing_value", "1"),
    (405, "framespacing_value", "0"),
    (407, "headers_value", "<elementid>"),
    (409, "height_value", "<fuzzint>"),
    (411, "hidden_value", "hidden"),
    (413, "high_value", "<fuzzint>"),
    (415, "href_value", "<attributestring>"),
    (417, "hreflang_value", "<lang_value>"),
    (419, "hspace_value", "<fuzzint>"),
    (421, "http-equiv_value", "Content-Security-Policy"),
    (422, "http-equiv_value", "Content-Language"),
    (423, "http-equiv_value", "Content-Style-Type"),
    (424, "http-equiv_value", "cache-control"),
    (425, "http-equiv_value", "Accept-CH"),
    (426, "http-equiv_value", "Content-Script-Type"),
    (427, "http-equiv_value", "Refresh"),
    (428, "http-equiv_value", "Content-Security-Policy-Report-Only"),
    (429, "http-equiv_value", "pragma"),
    (430, "http-equiv_value", "origin-trial"),
    (431, "http-equiv_value", "Suborigin"),
    (432, "http-equiv_value", "refresh"),
    (433, "http-equiv_value", "X-UA-Compatible"),
    (434, "http-equiv_value", "Content-Type"),
    (436, "icon_value", "<attributestring>"),
    (440, "incremental_value", "incremental"),
    (442, "indeterminate_value", "<boolean>"),
    (444, "inner_value", "1"),
    (446, "inputmode_value", "verbatim"),
    (447, "inputmode_value", "latin"),
    (448, "inputmode_value", "latin-name"),
    (449, "inputmode_value", "latin-prose"),
    (450, "inputmode_value", "full-width-latin"),
    (451, "inputmode_value", "kana"),
    (452, "inputmode_value", "katakana"),
    (453, "inputmode_value", "numeric"),
    (454, "inputmode_value", "tel"),
    (455, "inputmode_value", "email"),
    (456, "inputmode_value", "url"),
    (458, "is_value", "x-pass"),
    (459, "is_value", "x-body"),
    (460, "is_value", "x-bar"),
    (461, "is_value", "x-iframe"),
    (462, "is_value", "x-g"),
    (463, "is_value", "x-rect"),
    (464, "is_value", "x-a"),
    (465, "is_value", "x-x"),
    (466, "is_value", "x-green"),
    (467, "is_value", "x-y"),
    (469, "ismap_value", "ismap"),
    (471, "item_value", "<attributestring>"),
    (473, "itemid_value", "<attributestring>"),
    (475, "itemprop_value", "<attributestring>"),
    (477, "itemref_value", "<elementid>"),
    (479, "itemscope_value", "<attributestring>"),
    (481, "itemtype_value", "<attributestring>"),
    (483, "keytype_value", "RSA"),
    (485, "kind_value", "captions"),
    (486, "kind_value", "chapters"),
    (487, "kind_value", "descriptions"),
    (488, "kind_value", "metadata"),
    (489, "kind_value", "subtitles"),
    (491, "label_value", "<attributestring>"),
    (493, "lang_value", "fr-CH"),
    (494, "lang_value", "gu"),
    (495, "lang_value", "tr_TR"),
    (496, "lang_value", "fr-CA"),
    (497, "lang_value", "nd"),
    (498, "lang_value", "ru-ru"),
    (499, "lang_value", "rof"),
    (500, "lang_value", "zh-Hant"),
    (501, "lang_value", "shi"),
    (502, "lang_value", "sv"),
    (503, "lang_value", "twq"),
    (504, "lang_value", "mfe"),
    (505, "lang_value", "en-GB"),
    (506, "lang_value", "lg"),
    (507, "lang_value", "xog"),
    (508, "lang_value", "ln"),
    (509, "lang_value", "lo"),
    (510, "lang_value", "nus"),
    (511, "lang_value", "tr"),
    (512, "lang_value", "ko-kr"),
    (513, "lang_value", "de-CH"),
    (514, "lang_value", "CUSTOM"),
    (515, "lang_value", "lv"),
    (516, "lang_value", "en-au"),
    (517, "lang_value", "lt"),
    (518, "lang_value", "ksf"),
    (519, "lang_value", "dje"),
    (520, "lang_value", "th"),
    (521, "lang_value", "ksb"),
    (522, "lang_value", "he"),
    (523, "lang_value", "ta"),
    (524, "lang_value", "en-HanT"),
    (525, "lang_value", "en-HanS"),
    (526, "lang_value", "yo"),
    (527, "lang_value", "lt-LT"),
    (528, "lang_value", "ja-latn"),
    (529, "lang_value", "lt@foo=bar"),
    (530, "lang_value", "de"),
    (531, "lang_value", "mgo"),
    (532, "lang_value", "da"),
    (533, "lang_value", "dav"),
    (534, "lang_value", "dyo"),
    (535, "lang_value", "dz"),
    (536, "lang_value", "el-GR"),
    (537, "lang_value", "kkj"),
    (538, "lang_value", "ja-jp"),
    (539, "lang_value", "vi-vn"),
    (540, "lang_value", "qs"),
    (541, "lang_value", "ko-Hang"),
    (542, "lang_value", "bas"),
    (543, "lang_value", "tr@foo=bar"),
    (544, "lang_value", "zh-hant"),
    (545, "lang_value", "shi-Tfng"),
    (546, "lang_value", "guz"),
    (547, "lang_value", "ja-Hrkt"),
    (548, "lang_value", "sw"),
    (549, "lang_value", "el"),
    (550, "lang_value", "en"),
    (551, "lang_value", "zh"),
    (552, "lang_value", "az-AZ"),
    (553, "lang_value", "teo"),
    (554, "lang_value", "en-TW"),
    (555, "lang_value", "kde"),
    (556, "lang_value", "zh_Hans"),
    (557, "lang_value", "mas"),
    (558, "lang_value", "zh-HK"),
    (559, "lang_value", "bg"),
    (560, "lang_value", "el@foo=bar"),
    (561, "lang_value", "eu"),
    (562, "lang_value", "et"),
    (563, "lang_value", "vun"),
    (564, "lang_value", "es"),
    (565, "lang_value", "seh"),
    (566, "lang_value", "en-gb"),
    (567, "lang_value", "ru"),
    (568, "lang_value", "rw"),
    (569, "lang_value", "jgo"),
    (570, "lang_value", "ff"),
    (571, "lang_value", "luy"),
    (572, "lang_value", "chr"),
    (573, "lang_value", "ti-ER"),
    (574, "lang_value", "zh-cn"),
    (575, "lang_value", "mua"),
    (576, "lang_value", "und-Zxxx"),
    (577, "lang_value", "rn"),
    (578, "lang_value", "ro"),
    (579, "lang_value", "luo"),
    (580, "lang_value", "EL"),
    (581, "lang_value", "sbp"),
    (582, "lang_value", "az@foo=bar"),
    (583, "lang_value", "ko-Kore"),
    (584, "lang_value", "bm"),
    (585, "lang_value", "bn"),
    (586, "lang_value", "cgg"),
    (587, "lang_value", "az-Cyrl"),
    (588, "lang_value", "y-test"),
    (589, "lang_value", "nmg"),
    (590, "lang_value", "asa"),
    (591, "lang_value", "kea"),
    (592, "lang_value", "br"),
    (593, "lang_value", "mer"),
    (594, "lang_value", "ja"),
    (595, "lang_value", "ms"),
    (596, "lang_value", "ebu"),
    (597, "lang_value", "ko-KR"),
    (598, "lang_value", "LT"),
    (599, "lang_value", "en-fr"),
    (600, "lang_value", "dua"),
    (601, "lang_value", "en-GB-wa"),
    (602, "lang_value", "en-HanS-JP"),
    (603, "lang_value", "xh"),
    (604, "lang_value", "yav"),
    (605, "lang_value", "cn"),
    (606, "lang_value", "brx"),
    (607, "lang_value", "ca"),
    (608, "lang_value", "en-us"),
    (609, "lang_value", "en-CN"),
    (610, "lang_value", "xx"),
    (611, "lang_value", "en-KR"),
    (612, "lang_value", "cs"),
    (613, "lang_value", "ses"),
    (614, "lang_value", "x-test"),
    (615, "lang_value", "vai"),
    (616, "lang_value", "pt"),
    (617, "lang_value", "to"),
    (618, "lang_value", "az_AZ"),
    (619, "lang_value", "zh-tw"),
    (620, "lang_value", "swc"),
    (621, "lang_value", "nnh"),
    (622, "lang_value", "lt_LT"),
    (623, "lang_value", "custom"),
    (624, "lang_value", "ee"),
    (625, "lang_value", "de-de"),
    (626, "lang_value", "lu"),
    (627, "lang_value", "tr-TR"),
    (628, "lang_value", "vi"),
    (629, "lang_value", "ja-Jpan"),
    (630, "lang_value", "pt-PT"),
    (631, "lang_value", "he-il"),
    (632, "lang_value", "pl"),
    (633, "lang_value", "hr"),
    (634, "lang_value", "en-US"),
    (635, "lang_value", "hu"),
    (636, "lang_value", "hi"),
    (637, "lang_value", "en-JP"),
    (638, "lang_value", "jmc"),
    (639, "lang_value", "ha"),
    (640, "lang_value", "ja-Kana"),
    (641, "lang_value", "en-HanT-JP"),
    (642, "lang_value", "ar-eg"),
    (643, "lang_value", "mg"),
    (644, "lang_value", "te"),
    (645, "lang_value", "bem"),
    (646, "lang_value", "ml"),
    (647, "lang_value", "kln"),
    (648, "lang_value", "mk"),
    (649, "lang_value", "ur"),
    (650, "lang_value", "bez"),
    (651, "lang_value", "zh-CN"),
    (652, "lang_value", "sr"),
    (653, "lang_value", "sr-Latn"),
    (654, "lang_value", "tr-US"),
    (655, "lang_value", "mr"),
    (656, "lang_value", "my"),
    (657, "lang_value", "sq"),
    (658, "lang_value", "af"),
    (659, "lang_value", "khq"),
    (660, "lang_value", "ak"),
    (661, "lang_value", "am"),
    (662, "lang_value", "it"),
    (663, "lang_value", "en-Hixie"),
    (664, "lang_value", "ar-AE"),
    (665, "lang_value", "ar"),
    (666, "lang_value", "fr-fr"),
    (667, "lang_value", "zu"),
    (668, "lang_value", "el_GR"),
    (669, "lang_value", "saq"),
    (670, "lang_value", "ja-JP"),
    (671, "lang_value", "tzm"),
    (672, "lang_value", "id"),
    (673, "lang_value", "ig"),
    (674, "lang_value", "az"),
    (675, "lang_value", "nl"),
    (676, "lang_value", "nn"),
    (677, "lang_value", "no"),
    (678, "lang_value", "nb"),
    (679, "lang_value", "bs-Cyrl"),
    (680, "lang_value", "TR"),
    (681, "lang_value", "ne"),
    (682, "lang_value", "vai-Latn"),
    (683, "lang_value", "lt-US"),
    (684, "lang_value", "hi-in"),
    (685, "lang_value", "naq"),
    (686, "lang_value", "nyn"),
    (687, "lang_value", "z-test"),
    (688, "lang_value", "kab"),
    (689, "lang_value", "fr"),
    (690, "lang_value", "rwk"),
    (691, "lang_value", "en-HK"),
    (692, "lang_value", "lag"),
    (693, "lang_value", "kam"),
    (694, "lang_value", "fa"),
    (695, "lang_value", "ja-Hira"),
    (696, "lang_value", "el-gr"),
    (697, "lang_value", "x"),
    (698, "lang_value", "fi"),
    (699, "lang_value", "uk"),
    (700, "lang_value", "xyzzy"),
    (701, "lang_value", "gsw"),
    (702, "lang_value", "zh-TW"),
    (703, "lang_value", "ki"),
    (704, "lang_value", "ewo"),
    (705, "lang_value", "ko"),
    (706, "lang_value", "kn"),
    (707, "lang_value", "km"),
    (708, "lang_value", "sk"),
    (709, "lang_value", "si"),
    (710, "lang_value", "so"),
    (711, "lang_value", "sn"),
    (712, "lang_value", "sl"),
    (713, "lang_value", "sg"),
    (714, "lang_value", "el-US"),
    (715, "lang_value", "agq"),
    (717, "language_value", "javascript"),
    (718, "language_value", "vbscript"),
    (720, "layout_value", "auto"),
    (721, "layout_value", "fixed"),
    (723, "left_value", "<fuzzint>"),
    (725, "leftmargin_value", "<fuzzint>"),
    (727, "link_value", "<color>"),
    (729, "list_value", "<elementid>"),
    (731, "longdesc_value", "<attributestring>"),
    (733, "loop_value", "<fuzzint>"),
    (735, "loopend_value", "<fuzzint>"),
    (737, "loopstart_value", "<fuzzint>"),
    (739, "low_value", "<fuzzint>"),
    (741, "lowsrc_value", "<attributestring>"),
    (743, "manifest_value", "<attributestring>"),
    (745, "margin_value", "<fuzzint>px <fuzzint>px"),
    (747, "marginheight_value", "<fuzzint>"),
    (749, "marginwidth_value", "<fuzzint>"),
    (751, "max_value", "<fuzzint>"),
    (753, "maxlength_value", "<fuzzint>"),
    (755, "mayscript_value", "<boolean>"),
    (757, "media_value", "all"),
    (758, "media_value", "aural"),
    (759, "media_value", "braille"),
    (760, "media_value", "handheld"),
    (761, "media_value", "projection"),
    (762, "media_value", "print"),
    (763, "media_value", "screen"),
    (764, "media_value", "tty"),
    (765, "media_value", "tv"),
    (766, "media_value", "screen and (min-width:<fuzzint>px)"),
    (767, "media_value", "screen and (max-width:<fuzzint>px)"),
    (768, "media_value", "screen and (min-height:<fuzzint>px)"),
    (769, "media_value", "screen and (max-height:<fuzzint>px)"),
    (771, "menu_value", "<elementid>"),
    (773, "method_value", "get"),
    (774, "method_value", "put"),
    (775, "method_value", "post"),
    (777, "min_value", "<fuzzint>"),
    (779, "minlength_value", "<fuzzint>"),
    (781, "mode_value", "multiply"),
    (782, "mode_value", "screen"),
    (783, "mode_value", "showing"),
    (784, "mode_value", "normal"),
    (786, "multiple_value", "multiple"),
    (788, "muted_value", "muted"),
    (790, "name_value", "<attributestring>"),
    (792, "nohref_value", "nohref"),
    (794, "nonce_value", "nonce"),
    (796, "noresize_value", "noresize"),
    (798, "noshade_value", "noshade"),
    (800, "novalidate_value", "novalidate"),
    (802, "nowrap_value", "nowrap"),
    (804, "open_value", "true"),
    (806, "optimum_value", "<fuzzint>"),
    (808, "pattern_value", "<attributestring>"),
    (810, "ping_value", "<attributestring>"),
    (812, "placeholder_value", "<attributestring>"),
    (814, "playcount_value", "<fuzzint>"),
    (816, "pluginspage_value", "<attributestring>"),
    (818, "poster_value", "<attributestring>"),
    (820, "preload_value", "auto"),
    (821, "preload_value", "none"),
    (822, "preload_value", "metadata"),
    (824, "profile_value", "<attributestring>"),
    (826, "prompt_value", "<attributestring>"),
    (828, "quality_value", "high"),
    (830, "radiogroup_value", "group"),
    (832, "readonly_value", "readonly"),
    (834, "ref_value", "author"),
    (836, "referrerpolicy_value", "origin"),
    (837, "referrerpolicy_value", "unsafe-url"),
    (838, "referrerpolicy_value", "origin-when-crossorigin"),
    (839, "referrerpolicy_value", "never"),
    (840, "referrerpolicy_value", "no-referrer"),
    (841, "referrerpolicy_value", "no-referrer-when-downgrade"),
    (843, "rel_value", "alternate"),
    (844, "rel_value", "author"),
    (845, "rel_value", "bookmark"),
    (846, "rel_value", "external"),
    (847, "rel_value", "help"),
    (848, "rel_value", "license"),
    (849, "rel_value", "next"),
    (850, "rel_value", "nofollow"),
    (851, "rel_value", "noreferrer"),
    (852, "rel_value", "noopener"),
    (853, "rel_value", "prev"),
    (854, "rel_value", "search"),
    (855, "rel_value", "tag"),
    (857, "required_value", "required"),
    (859, "results_value", "<fuzzint>"),
    (861, "rev_value", "alternate"),
    (862, "rev_value", "stylesheet"),
    (863, "rev_value", "start"),
    (864, "rev_value", "next"),
    (865, "rev_value", "prev"),
    (866, "rev_value", "contents"),
    (867, "rev_value", "index"),
    (868, "rev_value", "glossary"),
    (869, "rev_value", "copyright"),
    (870, "rev_value", "chapter"),
    (871, "rev_value", "section"),
    (872, "rev_value", "subsection"),
    (873, "rev_value", "appendix"),
    (874, "rev_value", "help"),
    (875, "rev_value", "bookmark"),
    (877, "reversed_value", "reversed"),
    (879, "right_value", "<fuzzint>"),
    (881, "rightmargin_value", "<fuzzint>"),
    (883, "role_value", "treegrid"),
    (884, "role_value", "checkbox"),
    (885, "role_value", "radiogroup"),
    (886, "role_value", "text"),
    (887, "role_value", "menuitemcheckbox"),
    (888, "role_value", "spinbutton"),
    (889, "role_value", "radio"),
    (890, "role_value", "slider"),
    (891, "role_value", "tab"),
    (892, "role_value", "listbox"),
    (893, "role_value", "unknownrole checkbox"),
    (894, "role_value", "textbox"),
    (895, "role_value", "group"),
    (896, "role_value", "log"),
    (897, "role_value", "img"),
    (898, "role_value", "combobox"),
    (899, "role_value", "columnheader"),
    (900, "role_value", "tooltip"),
    (901, "role_value", "note"),
    (902, "role_value", "application"),
    (903, "role_value", "listitem"),
    (904, "role_value", "row"),
    (905, "role_value", "presentation"),
    (906, "role_value", "menuitem"),
    (907, "role_value", "searchbox"),
    (908, "role_value", "treeitem"),
    (909, "role_value", "status"),
    (910, "role_value", "main"),
    (911, "role_value", "rowheader"),
    (912, "role_value", "option"),
    (913, "role_value", "form"),
    (914, "role_value", "complementary"),
    (915, "role_value", "gridcell"),
    (916, "role_value", "contentinfo"),
    (917, "role_value", "none"),
    (918, "role_value", "alert"),
    (919, "role_value", "alertdialog"),
    (920, "role_value", "link"),
    (921, "role_value", "x"),
    (922, "role_value", "article"),
    (923, "role_value", "banner"),
    (924, "role_value", "toolbar"),
    (925, "role_value", "menuitemradio"),
    (926, "role_value", "definition"),
    (927, "role_value", "search"),
    (928, "role_value", "document"),
    (929, "role_value", "tree"),
    (930, "role_value", "marquee"),
    (931, "role_value", "menubar"),
    (932, "role_value", "button"),
    (933, "role_value", "list"),
    (934, "role_value", "timer"),
    (935, "role_value", "progressbar"),
    (936, "role_value", "scrollbar"),
    (937, "role_value", "separator"),
    (938, "role_value", "math"),
    (939, "role_value", "dialog"),
    (940, "role_value", "menu"),
    (941, "role_value", "directory"),
    (942, "role_value", "grid"),
    (943, "role_value", "tablist"),
    (944, "role_value", "region"),
    (945, "role_value", "navigation"),
    (946, "role_value", "heading"),
    (947, "role_value", "tabpanel"),
    (949, "row_value", "<fuzzint>"),
    (951, "rows_value", "<fuzzint>"),
    (952, "rows_value", "<fuzzint>,<fuzzint>"),
    (954, "rowspan_value", "<fuzzint>"),
    (956, "rules_value", "none"),
    (957, "rules_value", "rows"),
    (958, "rules_value", "all"),
    (959, "rules_value", "cols"),
    (960, "rules_value", "groups"),
    (962, "sandbox_value", "allowScripts allowSameOrigin allowFoobarbloop"),
    (963, "sandbox_value", "allow-forms allow-scripts"),
    (964, "sandbox_value", "allow-same-origin allow-scripts"),
    (965, "sandbox_value", "allow-script allow-same-origin"),
    (966, "sandbox_value", "allow-same-origin allow-forms allow-scripts"),
    (967, "sandbox_value", "AlLoW-sCrIpTs allow-same-origin"),
    (968, "sandbox_value", "allow-scripts allow-same-origin"),
    (969, "sandbox_value", "allow-pointer-lock allow-scripts"),
    (970, "sandbox_value", "allow-scripts allow-forms allow-same-origin"),
    (971, "sandbox_value", "allow-scripts allow-popups allow-forms"),
    (972, "sandbox_value", "allow-scripts allow-forms allow-same-origin allow-popups"),
    (973, "sandbox_value", "allow-scripts allow-top-navigation"),
    (974, "sandbox_value", "allow-same-origin"),
    (975, "sandbox_value", "allowScripts"),
    (976, "sandbox_value", "allowScripts allow-same-origin"),
    (977, "sandbox_value", "allow-scripts allow-same-origin allow-orientation-lock"),
    (978, "sandbox_value", "allows-cripts allow-same-origin"),
    (979, "sandbox_value", "aallow-scripts allow-same-origin"),
    (980, "sandbox_value", "allowscripts allow-same-origin"),
    (981, "sandbox_value", "allow-scriptss allow-same-origin"),
    (982, "sandbox_value", "allow-scripts allow-top-navigation allow-same-origin"),
    (983, "sandbox_value", "allow_scripts allow-same-origin"),
    (984, "sandbox_value", "allow-scripts allow-same-origin"),
    (985, "sandbox_value", "allow-scripts allow-popups"),
    (986, "sandbox_value", "allow-scripts allow-same-origin allow-presentation"),
    (987, "sandbox_value", "allow-scripts"),
    (989, "scheme_value", "NIST"),
    (991, "scope_value", "colgroup"),
    (992, "scope_value", "col"),
    (993, "scope_value", "rowgroup"),
    (994, "scope_value", "row"),
    (996, "scoped_value", "scoped"),
    (998, "scrollamount_value", "<fuzzint>"),
    (1000, "scrolldelay_value", "<fuzzint>"),
    (1002, "scrolling_value", "auto"),
    (1003, "scrolling_value", "no"),
    (1004, "scrolling_value", "yes"),
    (1006, "seamless_value", "seamless"),
    (1008, "seed_value", "<fuzzint>"),
    (1010, "select_value", ".<class>"),
    (1011, "select_value", "<hash><elementid>"),
    (1013, "selected_value", "selected"),
    (1015, "shape_value", "default"),
    (1016, "shape_value", "poly"),
    (1017, "shape_value", "rect"),
    (1018, "shape_value", "circle"),
    (1020, "shouldfocus_value", "false"),
    (1021, "shouldfocus_value", "true"),
    (1023, "size_value", "<fuzzint>"),
    (1025, "sizes_value", "<fuzzint>px"),
    (1026, "sizes_value", "<fuzzint>vw"),
    (1028, "slope_value", "-10000"),
    (1029, "slope_value", "0.5"),
    (1030, "slope_value", "1"),
    (1031, "slope_value", "10000"),
    (1032, "slope_value", "0"),
    (1034, "slot_value", "slot1"),
    (1035, "slot_value", "slot2"),
    (1037, "span_value", "<fuzzint>"),
    (1039, "spellcheck_value", "false"),
    (1040, "spellcheck_value", "true"),
    (1042, "src_value", "<imgsrc>"),
    (1043, "src_value", "<audiosrc>"),
    (1044, "src_value", "<videosrc>"),
    (1045, "src_value", "<framesrc>"),
    (1047, "srcdoc_value", "<attributestring>"),
    (1049, "srcset_value", "<attributestring>"),
    (1051, "srclang_value", "<lang_value>"),
    (1053, "standby_value", "<attributestring>"),
    (1055, "start_value", "<fuzzint>"),
    (1057, "startoffset_value", "<float>"),
    (1059, "startval_value", "<attributestring>"),
    (1061, "step_value", "<fuzzint>"),
    (1063, "style_value", "<import from=cssgrammar symbol=declaration5>"),
    (1065, "summary_value", "<attributestring>"),
    (1067, "tabindex_value", "<fuzzint>"),
    (1069, "target_value", "<elementid>"),
    (1071, "text_value", "<color>"),
    (1073, "title_value", "<attributestring>"),
    (1075, "topmargin_value", "<fuzzint>"),
    (1077, "translate_value", "yes"),
    (1078, "translate_value", "no"),
    (1080, "truespeed_value", "<boolean>"),
    (1082, "type_value", "checkbox"),
    (1083, "type_value", "decimal"),
    (1084, "type_value", "text"),
    (1085, "type_value", "image/png"),
    (1086, "type_value", "image/foo"),
    (1087, "type_value", "jscript  1.0 "),
    (1088, "type_value", "datetime"),
    (1089, "type_value", "disc"),
    (1090, "type_value", "radio"),
    (1091, "type_value", "application/x-jscript"),
    (1092, "type_value", "CHECKBOX"),
    (1093, "type_value", "application/x-non-existent"),
    (1094, "type_value", "javascript 1.0 x"),
    (1095, "type_value", "disk"),
    (1096, "type_value", "not-a-number"),
    (1097, "type_value", "text/x-javascript"),
    (1098, "type_value", "password"),
    (1099, "type_value", "text/JAVASCRIPT"),
    (1100, "type_value", "menu"),
    (1101, "type_value", "ecmascript 1"),
    (1102, "type_value", "disabled_javascript"),
    (1103, "type_value", "video/x-chicken-face"),
    (1104, "type_value", "video/webm"),
    (1105, "type_value", "video/quicktime"),
    (1106, "type_value", "input"),
    (1107, "type_value", "circle"),
    (1108, "type_value", "application/x-ecmascript"),
    (1109, "type_value", "bogus"),
    (1110, "type_value", "JavaScript 1.5"),
    (1111, "type_value", "javascript1.1"),
    (1112, "type_value", "upper-roman"),
    (1113, "type_value", "text/javascript "),
    (1114, "type_value", "JavaScript1.5"),
    (1115, "type_value", "text/javascript;x-test=abc"),
    (1116, "type_value", "x-shader/x-vertex"),
    (1117, "type_value", "text/livescript"),
    (1118, "type_value", "JavaScript1.0"),
    (1119, "type_value", "JavaScript1.3"),
    (1120, "type_value", "application/jscript"),
    (1121, "type_value", "turbulence"),
    (1122, "type_value", "text/javascript1"),
    (1123, "type_value", "month"),
    (1124, "type_value", "JavaScript1.2"),
    (1125, "type_value", "JavaScript 1.1.1"),
    (1126, "type_value", "xxxjavascriptxxx"),
    (1127, "type_value", "rotate"),
    (1128, "type_value", "text/javascript"),
    (1129, "type_value", "text/html"),
    (1130, "type_value", "button"),
    (1131, "type_value", "isindex"),
    (1132, "type_value", "application/x-missing-plugin"),
    (1133, "type_value", "text/x-ecmascript"),
    (1134, "type_value", "audio/x-chicken-face"),
    (1135, "type_value", "text/javascript1.0"),
    (1136, "type_value", "text/javascript1.1"),
    (1137, "type_value", "text/vbscript"),
    (1138, "type_value", "text/javascript1.4"),
    (1139, "type_value", "text/javascript1.5"),
    (1140, "type_value", "CIRCLE"),
    (1141, "type_value", "round"),
    (1142, "type_value", "jscript 1"),
    (1143, "type_value", "JavaScript 2.1"),
    (1144, "type_value", "text/xml"),
    (1145, "type_value", "tel"),
    (1146, "type_value", "FILE"),
    (1147, "type_value", "livescript 1"),
    (1148, "type_value", "text/ecmascript"),
    (1149, "type_value", "image/x-icon"),
    (1150, "type_value", "JAVASCRIPT"),
    (1151, "type_value", "module"),
    (1152, "type_value", "popup"),
    (1153, "type_value", "JavaScript1.1"),
    (1154, "type_value", "text/worker"),
    (1155, "type_value", "image/gif;encodings="),
    (1156, "type_value", "ROUND"),
    (1157, "type_value", "blue"),
    (1158, "type_value", "text/javascript1.2"),
    (1159, "type_value", "NONE"),
    (1160, "type_value", "scale"),
    (1161, "type_value", "matrix"),
    (1162, "type_value", "audio/x-higglety-pigglety"),
    (1163, "type_value", "text/javascript1.3"),
    (1164, "type_value", "something-not-js"),
    (1165, "type_value", "application/x-shockwave-flash"),
    (1166, "type_value", "ecmascript 1.0"),
    (1167, "type_value", "application/x-unavailable"),
    (1168, "type_value", "text/x-isolate"),
    (1169, "type_value", "text/vbs"),
    (1170, "type_value", "JavaScript 1.4"),
    (1171, "type_value", "unknown/unknown"),
    (1172, "type_value", "application/pdf"),
    (1173, "type_value", "application/xml"),
    (1174, "type_value", "text/javascript1.6"),
    (1175, "type_value", "image/webp"),
    (1176, "type_value", "time "),
    (1177, "type_value", "email"),
    (1178, "type_value", "date-time"),
    (1179, "type_value", "fractalNoise"),
    (1180, "type_value", "image/gif;encodings"),
    (1181, "type_value", "linear"),
    (1182, "type_value", "text/javaScript"),
    (1183, "type_value", "JavaScript 1.0"),
    (1184, "type_value", "javascript"),
    (1185, "type_value", "application/octet-stream"),
    (1186, "type_value", "application/x-blink-test-plugin'"),
    (1187, "type_value", "JavaScript 1.3"),
    (1188, "type_value", "toolbar"),
    (1189, "type_value", "datetime-local"),
    (1190, "type_value", "search"),
    (1191, "type_value", "lower-roman"),
    (1192, "type_value", "TEXT/JAVASCRIPT"),
    (1193, "type_value", "text/something-not-javascript"),
    (1194, "type_value", "BUTTON"),
    (1195, "type_value", "image/*"),
    (1196, "type_value", "video/mp4"),
    (1197, "type_value", "javascript_1.0"),
    (1198, "type_value", "range"),
    (1199, "type_value", "image/png;bla=bla"),
    (1200, "type_value", "application/not-a-real-applet"),
    (1201, "type_value", "context"),
    (1202, "type_value", "HIDDEN"),
    (1203, "type_value", "UPPER-ROMAN"),
    (1204, "type_value", "gamma"),
    (1205, "type_value", "video/mpeg; codecs='avc1.4D400C'"),
    (1206, "type_value", "image/svg+xml"),
    (1207, "type_value", "color"),
    (1208, "type_value", "image"),
    (1209, "type_value", "number"),
    (1210, "type_value", "JavaScript1.4.1"),
    (1211, "type_value", "image/x-unsupported"),
    (1212, "type_value", "JavaScript1.7"),
    (1213, "type_value", "livescript1.1"),
    (1214, "type_value", "table"),
    (1215, "type_value", "livescript 1.0"),
    (1216, "type_value", "application/x-webkit-test-webplugin-persistent"),
    (1217, "type_value", "video/blahblah"),
    (1218, "type_value", "gif"),
    (1219, "type_value", ".gif"),
    (1220, "type_value", "unknown"),
    (1221, "type_value", "*"),
    (1222, "type_value", "upper-alpha"),
    (1223, "type_value", "JavaScript 10.0"),
    (1224, "type_value", "submit"),
    (1225, "type_value", "image/gif"),
    (1226, "type_value", "application/javascript"),
    (1227, "type_value", "border: 4px solid black; padding: 10px;"),
    (1228, "type_value", "text/plain"),
    (1229, "type_value", "ConText"),
    (1230, "type_value", "translate"),
    (1231, "type_value", "cite"),
    (1232, "type_value", "JavaScript 10"),
    (1233, "type_value", "javascript/frame-script"),
    (1234, "type_value", "JavaScript"),
    (1235, "type_value", "1.0 javascript"),
    (1236, "type_value", "square"),
    (1237, "type_value", "DECIMAL"),
    (1238, "type_value", "text/"),
    (1239, "type_value", "application/x-invalid-type"),
    (1240, "type_value", "application/x-plugin-placeholder-test"),
    (1241, "type_value", "TEXT"),
    (1242, "type_value", "application/x-javascript"),
    (1243, "type_value", "SQUARE"),
    (1244, "type_value", "JavaScript 2"),
    (1245, "type_value", "LOWER-ROMAN"),
    (1246, "type_value", "textfield"),
    (1247, "type_value", "application/x-java-applet"),
    (1248, "type_value", "hidden"),
    (1249, "type_value", "3D'text/css'"),
    (1250, "type_value", "application/xhtml+xml"),
    (1251, "type_value", "reset"),
    (1252, "type_value", "application/x-blink-deprecated-test-plugin"),
    (1253, "type_value", "none"),
    (1254, "type_value", "text/JavaScript"),
    (1255, "type_value", "image/jp2"),
    (1256, "type_value", "image/gif image/png"),
    (1257, "type_value", "Text"),
    (1258, "type_value", "JavaScript1"),
    (1259, "type_value", "video/ogg"),
    (1260, "type_value", "SUBMIT"),
    (1261, "type_value", "image\\gif"),
    (1262, "type_value", "video/mpeg"),
    (1263, "type_value", "RADIO"),
    (1264, "type_value", "text/css"),
    (1265, "type_value", "application/javascript1.2"),
    (1266, "type_value", "image/gif "),
    (1267, "type_value", "application/x-blink-test-plugin"),
    (1268, "type_value", "image/jpeg"),
    (1269, "type_value", "jscript"),
    (1270, "type_value", "video/mp4; codecs='avc1.4D400C'"),
    (1271, "type_value", "telephone"),
    (1272, "type_value", "Submit"),
    (1273, "type_value", "LOWER-ALPHA"),
    (1274, "type_value", "jscript 1.0"),
    (1275, "type_value", "DISC"),
    (1276, "type_value", "file"),
    (1277, "type_value", "image/gif;"),
    (1278, "type_value", "application/x-no-such-plugin"),
    (1279, "type_value", "*/*"),
    (1280, "type_value", "x-shader/x-fragment"),
    (1281, "type_value", "data:text/html"),
    (1282, "type_value", "DISK"),
    (1283, "type_value", "application/x-xstandard"),
    (1284, "type_value", "text/html; charset=utf-8"),
    (1285, "type_value", "IMAGE"),
    (1286, "type_value", "ecmascript"),
    (1287, "type_value", "invalid"),
    (1288, "type_value", "html"),
    (1289, "type_value", "application/x-webkit-test-webplugin"),
    (1290, "type_value", "video/x-higglety-pigglety"),
    (1291, "type_value", "test"),
    (1292, "type_value", "text/invalid"),
    (1293, "type_value", "text/javascript;charset=UTF-8"),
    (1294, "type_value", "application/x-livescript"),
    (1295, "type_value", "week"),
    (1296, "type_value", "JavaScript 1.8"),
    (1297, "type_value", "JavaScript 1.9"),
    (1298, "type_value", "livescript"),
    (1299, "type_value", "JavaScript 1.6"),
    (1300, "type_value", "JavaScript 1.7"),
    (1301, "type_value", "application/livescript"),
    (1302, "type_value", "JavaScript 1.1"),
    (1303, "type_value", "3D'cite'"),
    (1304, "type_value", "lower-alpha"),
    (1305, "type_value", "application/nonexistent"),
    (1306, "type_value", "isolated/world"),
    (1307, "type_value", "Application/x-webkit-test-webplugin"),
    (1308, "type_value", "JavaScript1.6"),
    (1309, "type_value", "date"),
    (1310, "type_value", "data"),
    (1311, "type_value", "identity"),
    (1312, "type_value", "text/worklet"),
    (1313, "type_value", "JavaScript1.4"),
    (1314, "type_value", "url"),
    (1315, "type_value", "text/jscript"),
    (1316, "type_value", "UPPER-ALPHA"),
    (1317, "type_value", "application/x-javascript1.2"),
    (1318, "type_value", "video/ogg; codecs='theora,vorbis'"),
    (1319, "type_value", "image/bar"),
    (1320, "type_value", "image/gif, image/png"),
    (1321, "type_value", "block"),
    (1322, "type_value", "time"),
    (1323, "type_value", "JavaScript 1"),
    (1324, "type_value", "application/ecmascript"),
    (1326, "usemap_value", "<hash><elementid>"),
    (1328, "valign_value", "middle"),
    (1329, "valign_value", "top"),
    (1330, "valign_value", "bottom"),
    (1331, "valign_value", "baseline"),
    (1332, "valign_value", "center"),
    (1334, "value_value", "<attributestring>"),
    (1336, "valuetype_value", "ref"),
    (1338, "version_value", "1"),
    (1339, "version_value", "-//W3C//DTD HTML 4.01 Transitional//EN"),
    (1340, "version_value", "1.0"),
    (1341, "version_value", "1.1"),
    (1343, "vlink_value", "<color>"),
    (1345, "vspace_value", "<fuzzint>"),
    (1347, "width_value", "<fuzzint>"),
    (1349, "wrap_value", "soft"),
    (1350, "wrap_value", "hard"),
    (1351, "wrap_value", "off"),
)

TAG_ATTRIBUTE_RULES: Final[tuple[TableRule, ...]] = (
    (18, "a_attribute", "<attribute_rev>"),
    (19, "a_attribute", "<attribute_onerror>"),
    (20, "a_attribute", "<attribute_accesskey>"),
    (21, "a_attribute", "<attribute_onmousedown>"),
    (22, "a_attribute", "<attribute_disabled>"),
    (23, "a_attribute", "<attribute_shape>"),
    (24, "a_attribute", "<attribute_href>"),
    (25, "a_attribute", "<attribute_download>"),
    (26, "a_attribute", "<attribute_draggable>"),
    (27, "a_attribute", "<attribute_style>"),
    (28, "a_attribute", "<attribute_ondragover>"),
    (29, "a_attribute", "<attribute_title>"),
    (30, "a_attribute", "<attribute_charset>"),
    (31, "a_attribute", "<attribute_ping>"),
    (32, "a_attribute", "<attribute_ondragleave>"),
    (33, "a_attribute", "<attribute_class>"),
    (34, "a_attribute", "<attribute_width>"),
    (35, "a_attribute", "<attribute_role>"),
    (36, "a_attribute", "<attribute_onclick>"),
    (37, "a_attribute", "<attribute_onfocus>"),
    (38, "a_attribute", "<attribute_ondrag>"),
    (39, "a_attribute", "<attribute_type>"),
    (40, "a_attribute", "<attribute_ondragenter>"),
    (41, "a_attribute", "<attribute_ondragstart>"),
    (42, "a_attribute", "<attribute_rel>"),
    (43, "a_attribute", "<attribute_ondrop>"),
    (44, "a_attribute", "<attribute_ondragend>"),
    (45, "a_attribute", "<attribute_onmouseover>"),
    (46, "a_attribute", "<attribute_hreflang>"),
    (47, "a_attribute", "<attribute_name>"),
    (48, "a_attribute", "<attribute_oncontextmenu>"),
    (49, "a_attribute", "<attribute_contenteditable>"),
    (50, "a_attribute", "<attribute_target>"),
    (51, "a_attribute", "<attribute_referrerpolicy>"),
    (52, "a_attribute", "<attribute_height>"),
    (53, "a_attribute", "<attribute_coords>"),
    (54, "a_attribute", "<attribute_dir>"),
    (55, "a_attribute", "<attribute_tabindex>"),
    (56, "a_attributes", "<a_attribute> <a_attribute> <a_attribute> <a_attribute> <a_attribute>"),
    (58, "abbr_attribute", "<attribute_lang>"),
    (59, "abbr_attribute", "<attribute_style>"),
    (60, "abbr_attribute", "<attribute_contenteditable>"),
    (61, "abbr_attribute", "<attribute_title>"),
    (62, "abbr_attribute", "<attribute_accesskey>"),
    (63, "abbr_attribute", "<attribute_class>"),
    (64, "abbr_attribute", "<attribute_dir>"),
    (65, "abbr_attribute", "<attribute_tabindex>"),
    (66, "abbr_attributes", ("<abbr_attribute> <abbr_attribute> <abbr_attribute> <abbr_attribute> <abbr_attribute>")),
    (68, "acronym_attribute", "<attribute_lang>"),
    (69, "acronym_attribute", "<attribute_title>"),
    (70, "acronym_attribute", "<attribute_class>"),
    (71, "acronym_attribute", "<attribute_dir>"),
    (72, "acronym_attribute", "<attribute_tabindex>"),
    (
        73,
        "acronym_attributes",
        ("<acronym_attribute> <acronym_attribute> <acronym_attribute> <acronym_attribute> <acronym_attribute>"),
    ),
    (75, "address_attribute", "<attribute_lang>"),
    (76, "address_attribute", "<attribute_style>"),
    (77, "address_attribute", "<attribute_title>"),
    (78, "address_attribute", "<attribute_class>"),
    (79, "address_attribute", "<attribute_dir>"),
    (
        80,
        "address_attributes",
        ("<address_attribute> <address_attribute> <address_attribute> <address_attribute> <address_attribute>"),
    ),
    (82, "applet_attribute", "<attribute_code>"),
    (83, "applet_attribute", "<attribute_name>"),
    (84, "applet_attribute", "<attribute_class>"),
    (85, "applet_attribute", "<attribute_archive>"),
    (86, "applet_attribute", "<attribute_codebase>"),
    (87, "applet_attribute", "<attribute_width>"),
    (88, "applet_attribute", "<attribute_alt>"),
    (89, "applet_attribute", "<attribute_height>"),
    (90, "applet_attribute", "<attribute_type>"),
    (91, "applet_attribute", "<attribute_tabindex>"),
    (
        92,
        "applet_attributes",
        ("<applet_attribute> <applet_attribute> <applet_attribute> <applet_attribute> <applet_attribute>"),
    ),
    (94, "area_attribute", "<attribute_target>"),
    (95, "area_attribute", "<attribute_title>"),
    (96, "area_attribute", "<attribute_referrerpolicy>"),
    (97, "area_attribute", "<attribute_accesskey>"),
    (98, "area_attribute", "<attribute_onfocus>"),
    (99, "area_attribute", "<attribute_name>"),
    (100, "area_attribute", "<attribute_nohref>"),
    (101, "area_attribute", "<attribute_shape>"),
    (102, "area_attribute", "<attribute_href>"),
    (103, "area_attribute", "<attribute_coords>"),
    (104, "area_attribute", "<attribute_onmouseover>"),
    (105, "area_attribute", "<attribute_onclick>"),
    (106, "area_attribute", "<attribute_download>"),
    (107, "area_attribute", "<attribute_role>"),
    (108, "area_attribute", "<attribute_alt>"),
    (109, "area_attribute", "<attribute_tabindex>"),
    (110, "area_attributes", ("<area_attribute> <area_attribute> <area_attribute> <area_attribute> <area_attribute>")),
    (112, "article_attribute", "<attribute_style>"),
    (113, "article_attribute", "<attribute_name>"),
    (114, "article_attributes", "<article_attribute> <article_attribute>"),
    (116, "aside_attribute", "<attribute_class>"),
    (117, "aside_attribute", "<attribute_name>"),
    (118, "aside_attributes", "<aside_attribute> <aside_attribute>"),
    (120, "audio_attribute", "<attribute_preload>"),
    (121, "audio_attribute", "<attribute_audiosrc>"),
    (122, "audio_attribute", "<attribute_style>"),
    (123, "audio_attribute", "<attribute_name>"),
    (124, "audio_attribute", "<attribute_onfocus>"),
    (125, "audio_attribute", "<attribute_onerror>"),
    (126, "audio_attribute", "<attribute_controls>"),
    (127, "audio_attribute", "<attribute_class>"),
    (128, "audio_attribute", "<attribute_onplaying>"),
    (129, "audio_attribute", "<attribute_tabindex>"),
    (130, "audio_attribute", "<attribute_onloadstart>"),
    (131, "audio_attribute", "<attribute_autoplay>"),
    (
        132,
        "audio_attributes",
        ("<audio_attribute> <audio_attribute> <audio_attribute> <audio_attribute> <audio_attribute>"),
    ),
    (134, "b_attribute", "<attribute_lang>"),
    (135, "b_attribute", "<attribute_style>"),
    (136, "b_attribute", "<attribute_contenteditable>"),
    (137, "b_attribute", "<attribute_title>"),
    (138, "b_attribute", "<attribute_is>"),
    (139, "b_attribute", "<attribute_class>"),
    (140, "b_attribute", "<attribute_draggable>"),
    (141, "b_attribute", "<attribute_dir>"),
    (142, "b_attribute", "<attribute_tabindex>"),
    (143, "b_attributes", "<b_attribute> <b_attribute> <b_attribute> <b_attribute> <b_attribute>"),
    (145, "base_attribute", "<attribute_target>"),
    (146, "base_attribute", "<attribute_title>"),
    (147, "base_attribute", "<attribute_href>"),
    (148, "base_attribute", "<attribute_class>"),
    (149, "base_attribute", "<attribute_dir>"),
    (150, "base_attributes", ("<base_attribute> <base_attribute> <base_attribute> <base_attribute> <base_attribute>")),
    (152, "basefont_attribute", "<attribute_name>"),
    (153, "basefont_attribute", "<attribute_size>"),
    (154, "basefont_attributes", "<basefont_attribute> <basefont_attribute>"),
    (156, "bdi_attribute", "<attribute_style>"),
    (157, "bdi_attribute", "<attribute_dir>"),
    (158, "bdi_attributes", "<bdi_attribute> <bdi_attribute>"),
    (160, "bdo_attribute", "<attribute_lang>"),
    (161, "bdo_attribute", "<attribute_title>"),
    (162, "bdo_attribute", "<attribute_class>"),
    (163, "bdo_attribute", "<attribute_dir>"),
    (164, "bdo_attribute", "<attribute_tabindex>"),
    (165, "bdo_attributes", ("<bdo_attribute> <bdo_attribute> <bdo_attribute> <bdo_attribute> <bdo_attribute>")),
    (167, "big_attribute", "<attribute_lang>"),
    (168, "big_attribute", "<attribute_style>"),
    (169, "big_attribute", "<attribute_title>"),
    (170, "big_attribute", "<attribute_class>"),
    (171, "big_attribute", "<attribute_dir>"),
    (172, "big_attribute", "<attribute_tabindex>"),
    (173, "big_attributes", ("<big_attribute> <big_attribute> <big_attribute> <big_attribute> <big_attribute>")),
    (175, "blockquote_attribute", "<attribute_style>"),
    (176, "blockquote_attribute", "<attribute_title>"),
    (177, "blockquote_attribute", "<attribute_class>"),
    (178, "blockquote_attribute", "<attribute_type>"),
    (179, "blockquote_attribute", "<attribute_cite>"),
    (180, "blockquote_attribute", "<attribute_tabindex>"),
    (
        181,
        "blockquote_attributes",
        (
            "<blockquote_attribute> <blockquote_attri"
            "bute> <blockquote_attribute> <blockquote"
            "_attribute> <blockquote_attribute>"
        ),
    ),
    (183, "br_attribute", "<attribute_style>"),
    (184, "br_attribute", "<attribute_title>"),
    (185, "br_attribute", "<attribute_clear>"),
    (186, "br_attribute", "<attribute_class>"),
    (187, "br_attribute", "<attribute_tabindex>"),
    (188, "br_attributes", "<br_attribute> <br_attribute> <br_attribute> <br_attribute> <br_attribute>"),
    (190, "button_attribute", "<attribute_formmethod>"),
    (191, "button_attribute", "<attribute_onerror>"),
    (192, "button_attribute", "<attribute_accesskey>"),
    (193, "button_attribute", "<attribute_onfocus>"),
    (194, "button_attribute", "<attribute_disabled>"),
    (195, "button_attribute", "<attribute_autofocus>"),
    (196, "button_attribute", "<attribute_style>"),
    (197, "button_attribute", "<attribute_title>"),
    (198, "button_attribute", "<attribute_menu>"),
    (199, "button_attribute", "<attribute_formenctype>"),
    (200, "button_attribute", "<attribute_readonly>"),
    (201, "button_attribute", "<attribute_role>"),
    (202, "button_attribute", "<attribute_onclick>"),
    (203, "button_attribute", "<attribute_formtarget>"),
    (204, "button_attribute", "<attribute_hidden>"),
    (205, "button_attribute", "<attribute_type>"),
    (206, "button_attribute", "<attribute_formaction>"),
    (207, "button_attribute", "<attribute_onblur>"),
    (208, "button_attribute", "<attribute_onmouseup>"),
    (209, "button_attribute", "<attribute_form>"),
    (210, "button_attribute", "<attribute_onkeypress>"),
    (211, "button_attribute", "<attribute_formnovalidate>"),
    (212, "button_attribute", "<attribute_onmouseover>"),
    (213, "button_attribute", "<attribute_onmousedown>"),
    (214, "button_attribute", "<attribute_class>"),
    (215, "button_attribute", "<attribute_name>"),
    (216, "button_attribute", "<attribute_contextmenu>"),
    (217, "button_attribute", "<attribute_align>"),
    (218, "button_attribute", "<attribute_value>"),
    (219, "button_attribute", "<attribute_ondblclick>"),
    (220, "button_attribute", "<attribute_ontouchstart>"),
    (221, "button_attribute", "<attribute_tabindex>"),
    (
        222,
        "button_attributes",
        ("<button_attribute> <button_attribute> <button_attribute> <button_attribute> <button_attribute>"),
    ),
    (224, "canvas_attribute", "<attribute_style>"),
    (225, "canvas_attribute", "<attribute_contenteditable>"),
    (226, "canvas_attribute", "<attribute_name>"),
    (227, "canvas_attribute", "<attribute_src>"),
    (228, "canvas_attribute", "<attribute_width>"),
    (229, "canvas_attribute", "<attribute_hidden>"),
    (230, "canvas_attribute", "<attribute_height>"),
    (231, "canvas_attribute", "<attribute_class>"),
    (232, "canvas_attribute", "<attribute_dir>"),
    (233, "canvas_attribute", "<attribute_tabindex>"),
    (
        234,
        "canvas_attributes",
        ("<canvas_attribute> <canvas_attribute> <canvas_attribute> <canvas_attribute> <canvas_attribute>"),
    ),
    (236, "caption_attribute", "<attribute_style>"),
    (237, "caption_attribute", "<attribute_title>"),
    (238, "caption_attribute", "<attribute_align>"),
    (239, "caption_attribute", "<attribute_class>"),
    (240, "caption_attribute", "<attribute_dir>"),
    (241, "caption_attribute", "<attribute_tabindex>"),
    (
        242,
        "caption_attributes",
        ("<caption_attribute> <caption_attribute> <caption_attribute> <caption_attribute> <caption_attribute>"),
    ),
    (244, "center_attribute", "<attribute_lang>"),
    (245, "center_attribute", "<attribute_title>"),
    (246, "center_attribute", "<attribute_class>"),
    (247, "center_attribute", "<attribute_dir>"),
    (248, "center_attribute", "<attribute_tabindex>"),
    (
        249,
        "center_attributes",
        ("<center_attribute> <center_attribute> <center_attribute> <center_attribute> <center_attribute>"),
    ),
    (251, "cite_attribute", "<attribute_lang>"),
    (252, "cite_attribute", "<attribute_style>"),
    (253, "cite_attribute", "<attribute_title>"),
    (254, "cite_attribute", "<attribute_class>"),
    (255, "cite_attribute", "<attribute_dir>"),
    (256, "cite_attribute", "<attribute_tabindex>"),
    (257, "cite_attributes", ("<cite_attribute> <cite_attribute> <cite_attribute> <cite_attribute> <cite_attribute>")),
    (259, "code_attribute", "<attribute_lang>"),
    (260, "code_attribute", "<attribute_title>"),
    (261, "code_attribute", "<attribute_class>"),
    (262, "code_attribute", "<attribute_dir>"),
    (263, "code_attribute", "<attribute_tabindex>"),
    (264, "code_attributes", ("<code_attribute> <code_attribute> <code_attribute> <code_attribute> <code_attribute>")),
    (266, "col_attribute", "<attribute_bordercolor>"),
    (267, "col_attribute", "<attribute_style>"),
    (268, "col_attribute", "<attribute_span>"),
    (269, "col_attribute", "<attribute_align>"),
    (270, "col_attribute", "<attribute_class>"),
    (271, "col_attribute", "<attribute_char>"),
    (272, "col_attribute", "<attribute_width>"),
    (273, "col_attribute", "<attribute_valign>"),
    (274, "col_attribute", "<attribute_charoff>"),
    (275, "col_attribute", "<attribute_tabindex>"),
    (276, "col_attributes", ("<col_attribute> <col_attribute> <col_attribute> <col_attribute> <col_attribute>")),
    (278, "colgroup_attribute", "<attribute_bordercolor>"),
    (279, "colgroup_attribute", "<attribute_style>"),
    (280, "colgroup_attribute", "<attribute_span>"),
    (281, "colgroup_attribute", "<attribute_align>"),
    (282, "colgroup_attribute", "<attribute_class>"),
    (283, "colgroup_attribute", "<attribute_char>"),
    (284, "colgroup_attribute", "<attribute_width>"),
    (285, "colgroup_attribute", "<attribute_valign>"),
    (286, "colgroup_attribute", "<attribute_charoff>"),
    (287, "colgroup_attribute", "<attribute_tabindex>"),
    (
        288,
        "colgroup_attributes",
        ("<colgroup_attribute> <colgroup_attribute> <colgroup_attribute> <colgroup_attribute> <colgroup_attribute>"),
    ),
    (290, "command_attribute", "<attribute_onerror>"),
    (291, "command_attribute", "<attribute_name>"),
    (292, "command_attribute", "<attribute_icon>"),
    (293, "command_attributes", "<command_attribute> <command_attribute> <command_attribute>"),
    (295, "content_attribute", "<attribute_select>"),
    (296, "content_attributes", "<content_attribute>"),
    (298, "data_attribute", "<attribute_value>"),
    (299, "data_attributes", "<data_attribute>"),
    (301, "datalist_attribute", "<attribute_class>"),
    (302, "datalist_attribute", "<attribute_name>"),
    (303, "datalist_attributes", "<datalist_attribute> <datalist_attribute>"),
    (305, "dd_attribute", "<attribute_lang>"),
    (306, "dd_attribute", "<attribute_style>"),
    (307, "dd_attribute", "<attribute_contenteditable>"),
    (308, "dd_attribute", "<attribute_title>"),
    (309, "dd_attribute", "<attribute_class>"),
    (310, "dd_attribute", "<attribute_dir>"),
    (311, "dd_attribute", "<attribute_tabindex>"),
    (312, "dd_attributes", "<dd_attribute> <dd_attribute> <dd_attribute> <dd_attribute> <dd_attribute>"),
    (314, "del_attribute", "<attribute_style>"),
    (315, "del_attribute", "<attribute_title>"),
    (316, "del_attribute", "<attribute_class>"),
    (317, "del_attribute", "<attribute_cite>"),
    (318, "del_attribute", "<attribute_datetime>"),
    (319, "del_attribute", "<attribute_tabindex>"),
    (320, "del_attributes", ("<del_attribute> <del_attribute> <del_attribute> <del_attribute> <del_attribute>")),
    (322, "details_attribute", "<attribute_style>"),
    (323, "details_attribute", "<attribute_name>"),
    (324, "details_attribute", "<attribute_class>"),
    (325, "details_attribute", "<attribute_ontoggle>"),
    (326, "details_attribute", "<attribute_open>"),
    (
        327,
        "details_attributes",
        ("<details_attribute> <details_attribute> <details_attribute> <details_attribute> <details_attribute>"),
    ),
    (329, "dfn_attribute", "<attribute_lang>"),
    (330, "dfn_attribute", "<attribute_title>"),
    (331, "dfn_attribute", "<attribute_class>"),
    (332, "dfn_attribute", "<attribute_dir>"),
    (333, "dfn_attribute", "<attribute_tabindex>"),
    (334, "dfn_attributes", ("<dfn_attribute> <dfn_attribute> <dfn_attribute> <dfn_attribute> <dfn_attribute>")),
    (336, "dialog_attribute", "<attribute_style>"),
    (337, "dialog_attribute", "<attribute_name>"),
    (338, "dialog_attribute", "<attribute_class>"),
    (339, "dialog_attribute", "<attribute_open>"),
    (340, "dialog_attribute", "<attribute_tabindex>"),
    (
        341,
        "dialog_attributes",
        ("<dialog_attribute> <dialog_attribute> <dialog_attribute> <dialog_attribute> <dialog_attribute>"),
    ),
    (343, "dir_attribute", "<attribute_compact>"),
    (344, "dir_attribute", "<attribute_tabindex>"),
    (345, "dir_attributes", "<dir_attribute> <dir_attribute>"),
    (347, "div_attribute", "<attribute_text>"),
    (348, "div_attribute", "<attribute_alt>"),
    (349, "div_attribute", "<attribute_slot>"),
    (350, "div_attribute", "<attribute_onload>"),
    (351, "div_attribute", "<attribute_style>"),
    (352, "div_attribute", "<attribute_spellcheck>"),
    (353, "div_attribute", "<attribute_title>"),
    (354, "div_attribute", "<attribute_onmousemove>"),
    (355, "div_attribute", "<attribute_hidden>"),
    (356, "div_attribute", "<attribute_onanimationstart>"),
    (357, "div_attribute", "<attribute_onsubmit>"),
    (358, "div_attribute", "<attribute_onkeypress>"),
    (359, "div_attribute", "<attribute_onmouseover>"),
    (360, "div_attribute", "<attribute_onfocus>"),
    (361, "div_attribute", "<attribute_is>"),
    (362, "div_attribute", "<attribute_desc>"),
    (363, "div_attribute", "<attribute_contenteditable>"),
    (364, "div_attribute", "<attribute_name>"),
    (365, "div_attribute", "<attribute_onmouseenter>"),
    (366, "div_attribute", "<attribute_item>"),
    (367, "div_attribute", "<attribute_oninput>"),
    (368, "div_attribute", "<attribute_onkeyup>"),
    (369, "div_attribute", "<attribute_dir>"),
    (370, "div_attribute", "<attribute_onchange>"),
    (371, "div_attribute", "<attribute_onwheel>"),
    (372, "div_attribute", "<attribute_onwebkitanimationstart>"),
    (373, "div_attribute", "<attribute_ondragleave>"),
    (374, "div_attribute", "<attribute_rel>"),
    (375, "div_attribute", "<attribute_onkeydown>"),
    (376, "div_attribute", "<attribute_onwebkitanimationiteration>"),
    (377, "div_attribute", "<attribute_src>"),
    (378, "div_attribute", "<attribute_onanimationend>"),
    (379, "div_attribute", "<attribute_tabindex>"),
    (380, "div_attribute", "<attribute_height>"),
    (381, "div_attribute", "<attribute_onscroll>"),
    (382, "div_attribute", "<attribute_href>"),
    (383, "div_attribute", "<attribute_width>"),
    (384, "div_attribute", "<attribute_inner>"),
    (385, "div_attribute", "<attribute_onmouseup>"),
    (386, "div_attribute", "<attribute_ontransitionend>"),
    (387, "div_attribute", "<attribute_translate>"),
    (388, "div_attribute", "<attribute_type>"),
    (389, "div_attribute", "<attribute_onblur>"),
    (390, "div_attribute", "<attribute_ondrop>"),
    (391, "div_attribute", "<attribute_onwebkittransitionend>"),
    (392, "div_attribute", "<attribute_onmousewheel>"),
    (393, "div_attribute", "<attribute_ondragend>"),
    (394, "div_attribute", "<attribute_align>"),
    (395, "div_attribute", "<attribute_onerror>"),
    (396, "div_attribute", "<attribute_onwebkitanimationend>"),
    (397, "div_attribute", "<attribute_ondragenter>"),
    (398, "div_attribute", "<attribute_onanimationiteration>"),
    (399, "div_attribute", "<attribute_onmousedown>"),
    (400, "div_attribute", "<attribute_border>"),
    (401, "div_attribute", "<attribute_onmouseout>"),
    (402, "div_attribute", "<attribute_ondragover>"),
    (403, "div_attribute", "<attribute_readonly>"),
    (404, "div_attribute", "<attribute_role>"),
    (405, "div_attribute", "<attribute_onclick>"),
    (406, "div_attribute", "<attribute_ondragstart>"),
    (407, "div_attribute", "<attribute_description>"),
    (408, "div_attribute", "<attribute_onmouseleave>"),
    (409, "div_attribute", "<attribute_onpaste>"),
    (410, "div_attribute", "<attribute_data>"),
    (411, "div_attribute", "<attribute_class>"),
    (412, "div_attribute", "<attribute_lang>"),
    (413, "div_attribute", "<attribute_ondrag>"),
    (414, "div_attribute", "<attribute_contextmenu>"),
    (415, "div_attribute", "<attribute_ontouchstart>"),
    (416, "div_attribute", "<attribute_draggable>"),
    (417, "div_attribute", "<attribute_ondblclick>"),
    (418, "div_attribute", "<attribute_onselectstart>"),
    (419, "div_attributes", ("<div_attribute> <div_attribute> <div_attribute> <div_attribute> <div_attribute>")),
    (421, "dl_attribute", "<attribute_compact>"),
    (422, "dl_attribute", "<attribute_style>"),
    (423, "dl_attribute", "<attribute_title>"),
    (424, "dl_attribute", "<attribute_class>"),
    (425, "dl_attribute", "<attribute_tabindex>"),
    (426, "dl_attributes", "<dl_attribute> <dl_attribute> <dl_attribute> <dl_attribute> <dl_attribute>"),
    (428, "dt_attribute", "<attribute_lang>"),
    (429, "dt_attribute", "<attribute_style>"),
    (430, "dt_attribute", "<attribute_contenteditable>"),
    (431, "dt_attribute", "<attribute_title>"),
    (432, "dt_attribute", "<attribute_class>"),
    (433, "dt_attribute", "<attribute_dir>"),
    (434, "dt_attribute", "<attribute_tabindex>"),
    (435, "dt_attributes", "<dt_attribute> <dt_attribute> <dt_attribute> <dt_attribute> <dt_attribute>"),
    (437, "em_attribute", "<attribute_lang>"),
    (438, "em_attribute", "<attribute_style>"),
    (439, "em_attribute", "<attribute_title>"),
    (440, "em_attribute", "<attribute_class>"),
    (441, "em_attribute", "<attribute_dir>"),
    (442, "em_attribute", "<attribute_tabindex>"),
    (443, "em_attributes", "<em_attribute> <em_attribute> <em_attribute> <em_attribute> <em_attribute>"),
    (445, "embed_attribute", "<attribute_shouldfocus>"),
    (446, "embed_attribute", "<attribute_onerror>"),
    (447, "embed_attribute", "<attribute_height>"),
    (448, "embed_attribute", "<attribute_border>"),
    (449, "embed_attribute", "<attribute_onload>"),
    (450, "embed_attribute", "<attribute_style>"),
    (451, "embed_attribute", "<attribute_quality>"),
    (452, "embed_attribute", "<attribute_width>"),
    (453, "embed_attribute", "<attribute_valign>"),
    (454, "embed_attribute", "<attribute_hidden>"),
    (455, "embed_attribute", "<attribute_type>"),
    (456, "embed_attribute", "<attribute_pluginspage>"),
    (457, "embed_attribute", "<attribute_class>"),
    (458, "embed_attribute", "<attribute_src>"),
    (459, "embed_attribute", "<attribute_contenteditable>"),
    (460, "embed_attribute", "<attribute_name>"),
    (461, "embed_attribute", "<attribute_tabindex>"),
    (
        462,
        "embed_attributes",
        ("<embed_attribute> <embed_attribute> <embed_attribute> <embed_attribute> <embed_attribute>"),
    ),
    (464, "fieldset_attribute", "<attribute_style>"),
    (465, "fieldset_attribute", "<attribute_name>"),
    (466, "fieldset_attribute", "<attribute_form>"),
    (467, "fieldset_attribute", "<attribute_onerror>"),
    (468, "fieldset_attribute", "<attribute_disabled>"),
    (469, "fieldset_attribute", "<attribute_class>"),
    (470, "fieldset_attribute", "<attribute_dir>"),
    (471, "fieldset_attribute", "<attribute_tabindex>"),
    (
        472,
        "fieldset_attributes",
        ("<fieldset_attribute> <fieldset_attribute> <fieldset_attribute> <fieldset_attribute> <fieldset_attribute>"),
    ),
    (474, "figure_attribute", "<attribute_name>"),
    (475, "figure_attribute", "<attribute_title>"),
    (476, "figure_attributes", "<figure_attribute> <figure_attribute>"),
    (478, "font_attribute", "<attribute_style>"),
    (479, "font_attribute", "<attribute_title>"),
    (480, "font_attribute", "<attribute_color>"),
    (481, "font_attribute", "<attribute_face>"),
    (482, "font_attribute", "<attribute_tabindex>"),
    (483, "font_attribute", "<attribute_class>"),
    (484, "font_attribute", "<attribute_dir>"),
    (485, "font_attribute", "<attribute_size>"),
    (486, "font_attributes", ("<font_attribute> <font_attribute> <font_attribute> <font_attribute> <font_attribute>")),
    (488, "footer_attribute", "<attribute_style>"),
    (489, "footer_attribute", "<attribute_name>"),
    (490, "footer_attributes", "<footer_attribute> <footer_attribute>"),
    (492, "form_attribute", "<attribute_onreset>"),
    (493, "form_attribute", "<attribute_height>"),
    (494, "form_attribute", "<attribute_onchange>"),
    (495, "form_attribute", "<attribute_oninput>"),
    (496, "form_attribute", "<attribute_title>"),
    (497, "form_attribute", "<attribute_name>"),
    (498, "form_attribute", "<attribute_width>"),
    (499, "form_attribute", "<attribute_hidden>"),
    (500, "form_attribute", "<attribute_method>"),
    (501, "form_attribute", "<attribute_onsubmit>"),
    (502, "form_attribute", "<attribute_style>"),
    (503, "form_attribute", "<attribute_formaction>"),
    (504, "form_attribute", "<attribute_novalidate>"),
    (505, "form_attribute", "<attribute_class>"),
    (506, "form_attribute", "<attribute_enctype>"),
    (507, "form_attribute", "<attribute_target>"),
    (508, "form_attribute", "<attribute_autocomplete>"),
    (509, "form_attribute", "<attribute_tabindex>"),
    (510, "form_attributes", ("<form_attribute> <form_attribute> <form_attribute> <form_attribute> <form_attribute>")),
    (512, "frame_attribute", "<attribute_ondragenter>"),
    (513, "frame_attribute", "<attribute_onload>"),
    (514, "frame_attribute", "<attribute_style>"),
    (515, "frame_attribute", "<attribute_ondragover>"),
    (516, "frame_attribute", "<attribute_ondragleave>"),
    (517, "frame_attribute", "<attribute_ondrag>"),
    (518, "frame_attribute", "<attribute_ondragstart>"),
    (519, "frame_attribute", "<attribute_ondrop>"),
    (520, "frame_attribute", "<attribute_noresize>"),
    (521, "frame_attribute", "<attribute_framemargin>"),
    (522, "frame_attribute", "<attribute_marginheight>"),
    (523, "frame_attribute", "<attribute_scrolling>"),
    (524, "frame_attribute", "<attribute_frameborder>"),
    (525, "frame_attribute", "<attribute_ondragend>"),
    (526, "frame_attribute", "<attribute_framesrc>"),
    (527, "frame_attribute", "<attribute_onunload>"),
    (528, "frame_attribute", "<attribute_name>"),
    (529, "frame_attribute", "<attribute_framespacing>"),
    (530, "frame_attribute", "<attribute_allowfullscreen>"),
    (531, "frame_attribute", "<attribute_marginwidth>"),
    (532, "frame_attribute", "<attribute_longdesc>"),
    (
        533,
        "frame_attributes",
        ("<frame_attribute> <frame_attribute> <frame_attribute> <frame_attribute> <frame_attribute>"),
    ),
    (535, "frameset_attribute", "<attribute_bordercolor>"),
    (536, "frameset_attribute", "<attribute_onload>"),
    (537, "frameset_attribute", "<attribute_style>"),
    (538, "frameset_attribute", "<attribute_rows>"),
    (539, "frameset_attribute", "<attribute_cols>"),
    (540, "frameset_attribute", "<attribute_framespacing>"),
    (541, "frameset_attribute", "<attribute_onmousedown>"),
    (542, "frameset_attribute", "<attribute_onmousemove>"),
    (543, "frameset_attribute", "<attribute_noresize>"),
    (544, "frameset_attribute", "<attribute_onmouseup>"),
    (545, "frameset_attribute", "<attribute_border>"),
    (546, "frameset_attribute", "<attribute_frameborder>"),
    (
        547,
        "frameset_attributes",
        ("<frameset_attribute> <frameset_attribute> <frameset_attribute> <frameset_attribute> <frameset_attribute>"),
    ),
    (549, "h1_attribute", "<attribute_style>"),
    (550, "h1_attribute", "<attribute_contenteditable>"),
    (551, "h1_attribute", "<attribute_lang>"),
    (552, "h1_attribute", "<attribute_title>"),
    (553, "h1_attribute", "<attribute_align>"),
    (554, "h1_attribute", "<attribute_class>"),
    (555, "h1_attribute", "<attribute_role>"),
    (556, "h1_attribute", "<attribute_accesskey>"),
    (557, "h1_attribute", "<attribute_onclick>"),
    (558, "h1_attribute", "<attribute_tabindex>"),
    (559, "h1_attribute", "<attribute_dir>"),
    (560, "h1_attributes", "<h1_attribute> <h1_attribute> <h1_attribute> <h1_attribute> <h1_attribute>"),
    (562, "h2_attribute", "<attribute_style>"),
    (563, "h2_attribute", "<attribute_title>"),
    (564, "h2_attribute", "<attribute_align>"),
    (565, "h2_attribute", "<attribute_role>"),
    (566, "h2_attribute", "<attribute_class>"),
    (567, "h2_attribute", "<attribute_tabindex>"),
    (568, "h2_attributes", "<h2_attribute> <h2_attribute> <h2_attribute> <h2_attribute> <h2_attribute>"),
    (570, "h3_attribute", "<attribute_style>"),
    (571, "h3_attribute", "<attribute_title>"),
    (572, "h3_attribute", "<attribute_align>"),
    (573, "h3_attribute", "<attribute_class>"),
    (574, "h3_attribute", "<attribute_role>"),
    (575, "h3_attribute", "<attribute_tabindex>"),
    (576, "h3_attributes", "<h3_attribute> <h3_attribute> <h3_attribute> <h3_attribute> <h3_attribute>"),
    (578, "h4_attribute", "<attribute_style>"),
    (579, "h4_attribute", "<attribute_title>"),
    (580, "h4_attribute", "<attribute_align>"),
    (581, "h4_attribute", "<attribute_role>"),
    (582, "h4_attribute", "<attribute_class>"),
    (583, "h4_attribute", "<attribute_tabindex>"),
    (584, "h4_attributes", "<h4_attribute> <h4_attribute> <h4_attribute> <h4_attribute> <h4_attribute>"),
    (586, "h5_attribute", "<attribute_align>"),
    (587, "h5_attribute", "<attribute_role>"),
    (588, "h5_attribute", "<attribute_class>"),
    (589, "h5_attribute", "<attribute_tabindex>"),
    (590, "h5_attributes", "<h5_attribute> <h5_attribute> <h5_attribute> <h5_attribute>"),
    (592, "h6_attribute", "<attribute_align>"),
    (593, "h6_attribute", "<attribute_role>"),
    (594, "h6_attribute", "<attribute_class>"),
    (595, "h6_attribute", "<attribute_tabindex>"),
    (596, "h6_attributes", "<h6_attribute> <h6_attribute> <h6_attribute> <h6_attribute>"),
    (598, "header_attribute", "<attribute_style>"),
    (599, "header_attribute", "<attribute_selected>"),
    (600, "header_attribute", "<attribute_name>"),
    (601, "header_attributes", "<header_attribute> <header_attribute> <header_attribute>"),
    (603, "hgroup_attribute", "<attribute_name>"),
    (604, "hgroup_attributes", "<hgroup_attribute>"),
    (606, "hr_attribute", "<attribute_style>"),
    (607, "hr_attribute", "<attribute_contenteditable>"),
    (608, "hr_attribute", "<attribute_color>"),
    (609, "hr_attribute", "<attribute_align>"),
    (610, "hr_attribute", "<attribute_title>"),
    (611, "hr_attribute", "<attribute_height>"),
    (612, "hr_attribute", "<attribute_width>"),
    (613, "hr_attribute", "<attribute_noshade>"),
    (614, "hr_attribute", "<attribute_class>"),
    (615, "hr_attribute", "<attribute_alt>"),
    (616, "hr_attribute", "<attribute_tabindex>"),
    (617, "hr_attribute", "<attribute_border>"),
    (618, "hr_attribute", "<attribute_size>"),
    (619, "hr_attributes", "<hr_attribute> <hr_attribute> <hr_attribute> <hr_attribute> <hr_attribute>"),
    (621, "i_attribute", "<attribute_lang>"),
    (622, "i_attribute", "<attribute_style>"),
    (623, "i_attribute", "<attribute_title>"),
    (624, "i_attribute", "<attribute_class>"),
    (625, "i_attribute", "<attribute_dir>"),
    (626, "i_attribute", "<attribute_tabindex>"),
    (627, "i_attributes", "<i_attribute> <i_attribute> <i_attribute> <i_attribute> <i_attribute>"),
    (629, "iframe_attribute", "<attribute_onerror>"),
    (630, "iframe_attribute", "<attribute_is>"),
    (631, "iframe_attribute", "<attribute_height>"),
    (632, "iframe_attribute", "<attribute_border>"),
    (633, "iframe_attribute", "<attribute_onload>"),
    (634, "iframe_attribute", "<attribute_style>"),
    (635, "iframe_attribute", "<attribute_seamless>"),
    (636, "iframe_attribute", "<attribute_width>"),
    (637, "iframe_attribute", "<attribute_frameborder>"),
    (638, "iframe_attribute", "<attribute_type>"),
    (639, "iframe_attribute", "<attribute_onmouseout>"),
    (640, "iframe_attribute", "<attribute_marginwidth>"),
    (641, "iframe_attribute", "<attribute_noresize>"),
    (642, "iframe_attribute", "<attribute_marginheight>"),
    (643, "iframe_attribute", "<attribute_scrolling>"),
    (644, "iframe_attribute", "<attribute_class>"),
    (645, "iframe_attribute", "<attribute_srcdoc>"),
    (646, "iframe_attribute", "<attribute_framesrc>"),
    (647, "iframe_attribute", "<attribute_onunload>"),
    (648, "iframe_attribute", "<attribute_hidden>"),
    (649, "iframe_attribute", "<attribute_name>"),
    (650, "iframe_attribute", "<attribute_referrerpolicy>"),
    (651, "iframe_attribute", "<attribute_align>"),
    (652, "iframe_attribute", "<attribute_onmouseover>"),
    (653, "iframe_attribute", "<attribute_sandbox>"),
    (654, "iframe_attribute", "<attribute_allowfullscreen>"),
    (655, "iframe_attribute", "<attribute_srcset>"),
    (656, "iframe_attribute", "<attribute_longdesc>"),
    (657, "iframe_attribute", "<attribute_tabindex>"),
    (
        658,
        "iframe_attributes",
        ("<iframe_attribute> <iframe_attribute> <iframe_attribute> <iframe_attribute> <iframe_attribute>"),
    ),
    (660, "image_attribute", "<attribute_onerror>"),
    (661, "image_attribute", "<attribute_height>"),
    (662, "image_attribute", "<attribute_href>"),
    (663, "image_attribute", "<attribute_alt>"),
    (664, "image_attribute", "<attribute_onload>"),
    (665, "image_attribute", "<attribute_style>"),
    (666, "image_attribute", "<attribute_title>"),
    (667, "image_attribute", "<attribute_width>"),
    (668, "image_attribute", "<attribute_class>"),
    (669, "image_attribute", "<attribute_src>"),
    (670, "image_attribute", "<attribute_name>"),
    (671, "image_attribute", "<attribute_srcset>"),
    (672, "image_attribute", "<attribute_tabindex>"),
    (
        673,
        "image_attributes",
        ("<image_attribute> <image_attribute> <image_attribute> <image_attribute> <image_attribute>"),
    ),
    (675, "img_attribute", "<attribute_crossorigin>"),
    (676, "img_attribute", "<attribute_onerror>"),
    (677, "img_attribute", "<attribute_height>"),
    (678, "img_attribute", "<attribute_disabled>"),
    (679, "img_attribute", "<attribute_usemap>"),
    (680, "img_attribute", "<attribute_alt>"),
    (681, "img_attribute", "<attribute_border>"),
    (682, "img_attribute", "<attribute_onload>"),
    (683, "img_attribute", "<attribute_style>"),
    (684, "img_attribute", "<attribute_title>"),
    (685, "img_attribute", "<attribute_hspace>"),
    (686, "img_attribute", "<attribute_width>"),
    (687, "img_attribute", "<attribute_onmouseup>"),
    (688, "img_attribute", "<attribute_role>"),
    (689, "img_attribute", "<attribute_onclick>"),
    (690, "img_attribute", "<attribute_ondrag>"),
    (691, "img_attribute", "<attribute_type>"),
    (692, "img_attribute", "<attribute_ondragstart>"),
    (693, "img_attribute", "<attribute_form>"),
    (694, "img_attribute", "<attribute_onunload>"),
    (695, "img_attribute", "<attribute_itemprop>"),
    (696, "img_attribute", "<attribute_ismap>"),
    (697, "img_attribute", "<attribute_class>"),
    (698, "img_attribute", "<attribute_imgsrc>"),
    (699, "img_attribute", "<attribute_hidden>"),
    (700, "img_attribute", "<attribute_name>"),
    (701, "img_attribute", "<attribute_lowsrc>"),
    (702, "img_attribute", "<attribute_sizes>"),
    (703, "img_attribute", "<attribute_referrerpolicy>"),
    (704, "img_attribute", "<attribute_align>"),
    (705, "img_attribute", "<attribute_draggable>"),
    (706, "img_attribute", "<attribute_vspace>"),
    (707, "img_attribute", "<attribute_srcset>"),
    (708, "img_attribute", "<attribute_longdesc>"),
    (709, "img_attribute", "<attribute_dir>"),
    (710, "img_attribute", "<attribute_tabindex>"),
    (711, "img_attributes", ("<img_attribute> <img_attribute> <img_attribute> <img_attribute> <img_attribute>")),
    (713, "input_attribute", "<attribute_results>"),
    (714, "input_attribute", "<attribute_disabled>"),
    (715, "input_attribute", "<attribute_usemap>"),
    (716, "input_attribute", "<attribute_alt>"),
    (717, "input_attribute", "<attribute_slot>"),
    (718, "input_attribute", "<attribute_style>"),
    (719, "input_attribute", "<attribute_spellcheck>"),
    (720, "input_attribute", "<attribute_title>"),
    (721, "input_attribute", "<attribute_onmousemove>"),
    (722, "input_attribute", "<attribute_onkeypress>"),
    (723, "input_attribute", "<attribute_formnovalidate>"),
    (724, "input_attribute", "<attribute_focus>"),
    (725, "input_attribute", "<attribute_onsearch>"),
    (726, "input_attribute", "<attribute_contenteditable>"),
    (727, "input_attribute", "<attribute_name>"),
    (728, "input_attribute", "<attribute_list>"),
    (729, "input_attribute", "<attribute_onkeyup>"),
    (730, "input_attribute", "<attribute_dir>"),
    (731, "input_attribute", "<attribute_itemprop>"),
    (732, "input_attribute", "<attribute_onchange>"),
    (733, "input_attribute", "<attribute_hspace>"),
    (734, "input_attribute", "<attribute_pattern>"),
    (735, "input_attribute", "<attribute_selected>"),
    (736, "input_attribute", "<attribute_onselect>"),
    (737, "input_attribute", "<attribute_formtarget>"),
    (738, "input_attribute", "<attribute_onfocus>"),
    (739, "input_attribute", "<attribute_oninput>"),
    (740, "input_attribute", "<attribute_onkeydown>"),
    (741, "input_attribute", "<attribute_step>"),
    (742, "input_attribute", "<attribute_placeholder>"),
    (743, "input_attribute", "<attribute_src>"),
    (744, "input_attribute", "<attribute_maxlength>"),
    (745, "input_attribute", "<attribute_tabindex>"),
    (746, "input_attribute", "<attribute_accesskey>"),
    (747, "input_attribute", "<attribute_height>"),
    (748, "input_attribute", "<attribute_oninvalid>"),
    (749, "input_attribute", "<attribute_size>"),
    (750, "input_attribute", "<attribute_checked>"),
    (751, "input_attribute", "<attribute_width>"),
    (752, "input_attribute", "<attribute_onmouseup>"),
    (753, "input_attribute", "<attribute_type>"),
    (754, "input_attribute", "<attribute_onblur>"),
    (755, "input_attribute", "<attribute_form>"),
    (756, "input_attribute", "<attribute_formaction>"),
    (757, "input_attribute", "<attribute_oncontextmenu>"),
    (758, "input_attribute", "<attribute_default>"),
    (759, "input_attribute", "<attribute_align>"),
    (760, "input_attribute", "<attribute_value>"),
    (761, "input_attribute", "<attribute_autocomplete>"),
    (762, "input_attribute", "<attribute_vspace>"),
    (763, "input_attribute", "<attribute_formmethod>"),
    (764, "input_attribute", "<attribute_onerror>"),
    (765, "input_attribute", "<attribute_accept>"),
    (766, "input_attribute", "<attribute_onmousedown>"),
    (767, "input_attribute", "<attribute_incremental>"),
    (768, "input_attribute", "<attribute_border>"),
    (769, "input_attribute", "<attribute_dirname>"),
    (770, "input_attribute", "<attribute_min>"),
    (771, "input_attribute", "<attribute_minlength>"),
    (772, "input_attribute", "<attribute_formenctype>"),
    (773, "input_attribute", "<attribute_readonly>"),
    (774, "input_attribute", "<attribute_role>"),
    (775, "input_attribute", "<attribute_onclick>"),
    (776, "input_attribute", "<attribute_startval>"),
    (777, "input_attribute", "<attribute_autofocus>"),
    (778, "input_attribute", "<attribute_multiple>"),
    (779, "input_attribute", "<attribute_max>"),
    (780, "input_attribute", "<attribute_onpaste>"),
    (781, "input_attribute", "<attribute_class>"),
    (782, "input_attribute", "<attribute_lang>"),
    (783, "input_attribute", "<attribute_ontransitionend>"),
    (784, "input_attribute", "<attribute_contextmenu>"),
    (785, "input_attribute", "<attribute_required>"),
    (786, "input_attribute", "<attribute_onabort>"),
    (787, "input_attribute", "<attribute_draggable>"),
    (788, "input_attribute", "<attribute_onselectstart>"),
    (
        789,
        "input_attributes",
        ("<input_attribute> <input_attribute> <input_attribute> <input_attribute> <input_attribute>"),
    ),
    (791, "ins_attribute", "<attribute_cite>"),
    (792, "ins_attribute", "<attribute_title>"),
    (793, "ins_attribute", "<attribute_class>"),
    (794, "ins_attribute", "<attribute_datetime>"),
    (795, "ins_attribute", "<attribute_tabindex>"),
    (796, "ins_attributes", ("<ins_attribute> <ins_attribute> <ins_attribute> <ins_attribute> <ins_attribute>")),
    (798, "kbd_attribute", "<attribute_lang>"),
    (799, "kbd_attribute", "<attribute_title>"),
    (800, "kbd_attribute", "<attribute_class>"),
    (801, "kbd_attribute", "<attribute_dir>"),
    (802, "kbd_attribute", "<attribute_tabindex>"),
    (803, "kbd_attributes", ("<kbd_attribute> <kbd_attribute> <kbd_attribute> <kbd_attribute> <kbd_attribute>")),
    (805, "keygen_attribute", "<attribute_style>"),
    (806, "keygen_attribute", "<attribute_name>"),
    (807, "keygen_attribute", "<attribute_form>"),
    (808, "keygen_attribute", "<attribute_onerror>"),
    (809, "keygen_attribute", "<attribute_keytype>"),
    (810, "keygen_attribute", "<attribute_autofocus>"),
    (811, "keygen_attribute", "<attribute_challenge>"),
    (
        812,
        "keygen_attributes",
        ("<keygen_attribute> <keygen_attribute> <keygen_attribute> <keygen_attribute> <keygen_attribute>"),
    ),
    (814, "label_attribute", "<attribute_style>"),
    (815, "label_attribute", "<attribute_contenteditable>"),
    (816, "label_attribute", "<attribute_form>"),
    (817, "label_attribute", "<attribute_for>"),
    (818, "label_attribute", "<attribute_onerror>"),
    (819, "label_attribute", "<attribute_accesskey>"),
    (820, "label_attribute", "<attribute_class>"),
    (821, "label_attribute", "<attribute_disabled>"),
    (822, "label_attribute", "<attribute_onclick>"),
    (823, "label_attribute", "<attribute_tabindex>"),
    (
        824,
        "label_attributes",
        ("<label_attribute> <label_attribute> <label_attribute> <label_attribute> <label_attribute>"),
    ),
    (826, "legend_attribute", "<attribute_style>"),
    (827, "legend_attribute", "<attribute_align>"),
    (828, "legend_attribute", "<attribute_class>"),
    (829, "legend_attribute", "<attribute_accesskey>"),
    (830, "legend_attribute", "<attribute_tabindex>"),
    (
        831,
        "legend_attributes",
        ("<legend_attribute> <legend_attribute> <legend_attribute> <legend_attribute> <legend_attribute>"),
    ),
    (833, "li_attribute", "<attribute_lang>"),
    (834, "li_attribute", "<attribute_style>"),
    (835, "li_attribute", "<attribute_contenteditable>"),
    (836, "li_attribute", "<attribute_title>"),
    (837, "li_attribute", "<attribute_role>"),
    (838, "li_attribute", "<attribute_onclick>"),
    (839, "li_attribute", "<attribute_value>"),
    (840, "li_attribute", "<attribute_type>"),
    (841, "li_attribute", "<attribute_class>"),
    (842, "li_attribute", "<attribute_dir>"),
    (843, "li_attribute", "<attribute_tabindex>"),
    (844, "li_attributes", "<li_attribute> <li_attribute> <li_attribute> <li_attribute> <li_attribute>"),
    (846, "link_attribute", "<attribute_charset>"),
    (847, "link_attribute", "<attribute_crossorigin>"),
    (848, "link_attribute", "<attribute_onerror>"),
    (849, "link_attribute", "<attribute_hreflang>"),
    (850, "link_attribute", "<attribute_disabled>"),
    (851, "link_attribute", "<attribute_as>"),
    (852, "link_attribute", "<attribute_href>"),
    (853, "link_attribute", "<attribute_onload>"),
    (854, "link_attribute", "<attribute_title>"),
    (855, "link_attribute", "<attribute_media>"),
    (856, "link_attribute", "<attribute_content>"),
    (857, "link_attribute", "<attribute_rel>"),
    (858, "link_attribute", "<attribute_type>"),
    (859, "link_attribute", "<attribute_rev>"),
    (860, "link_attribute", "<attribute_src>"),
    (861, "link_attribute", "<attribute_ref>"),
    (862, "link_attribute", "<attribute_target>"),
    (863, "link_attribute", "<attribute_sizes>"),
    (864, "link_attribute", "<attribute_async>"),
    (865, "link_attributes", ("<link_attribute> <link_attribute> <link_attribute> <link_attribute> <link_attribute>")),
    (867, "listing_attribute", "<attribute_width>"),
    (868, "listing_attributes", "<listing_attribute>"),
    (870, "main_attribute", "<attribute_tabindex>"),
    (871, "main_attributes", "<main_attribute>"),
    (873, "map_attribute", "<attribute_tabindex>"),
    (874, "map_attribute", "<attribute_title>"),
    (875, "map_attribute", "<attribute_name>"),
    (876, "map_attribute", "<attribute_abbr>"),
    (877, "map_attributes", "<map_attribute> <map_attribute> <map_attribute> <map_attribute>"),
    (879, "mark_attribute", "<attribute_name>"),
    (880, "mark_attributes", "<mark_attribute>"),
    (882, "marquee_attribute", "<attribute_direction>"),
    (883, "marquee_attribute", "<attribute_scrolldelay>"),
    (884, "marquee_attribute", "<attribute_truespeed>"),
    (885, "marquee_attribute", "<attribute_style>"),
    (886, "marquee_attribute", "<attribute_height>"),
    (887, "marquee_attribute", "<attribute_width>"),
    (888, "marquee_attribute", "<attribute_scrollamount>"),
    (889, "marquee_attribute", "<attribute_behavior>"),
    (890, "marquee_attribute", "<attribute_bgcolor>"),
    (891, "marquee_attribute", "<attribute_loop>"),
    (892, "marquee_attribute", "<attribute_tabindex>"),
    (
        893,
        "marquee_attributes",
        ("<marquee_attribute> <marquee_attribute> <marquee_attribute> <marquee_attribute> <marquee_attribute>"),
    ),
    (895, "menu_attribute", "<attribute_style>"),
    (896, "menu_attribute", "<attribute_name>"),
    (897, "menu_attribute", "<attribute_compact>"),
    (898, "menu_attribute", "<attribute_onbeforecut>"),
    (899, "menu_attribute", "<attribute_role>"),
    (900, "menu_attribute", "<attribute_label>"),
    (901, "menu_attribute", "<attribute_type>"),
    (902, "menu_attribute", "<attribute_tabindex>"),
    (903, "menu_attributes", ("<menu_attribute> <menu_attribute> <menu_attribute> <menu_attribute> <menu_attribute>")),
    (905, "menuitem_attribute", "<attribute_checked>"),
    (906, "menuitem_attribute", "<attribute_default>"),
    (907, "menuitem_attribute", "<attribute_label>"),
    (908, "menuitem_attribute", "<attribute_disabled>"),
    (909, "menuitem_attribute", "<attribute_radiogroup>"),
    (910, "menuitem_attribute", "<attribute_onclick>"),
    (911, "menuitem_attribute", "<attribute_type>"),
    (912, "menuitem_attribute", "<attribute_icon>"),
    (
        913,
        "menuitem_attributes",
        ("<menuitem_attribute> <menuitem_attribute> <menuitem_attribute> <menuitem_attribute> <menuitem_attribute>"),
    ),
    (915, "meta_attribute", "<attribute_text>"),
    (916, "meta_attribute", "<attribute_title>"),
    (917, "meta_attribute", "<attribute_charset>"),
    (918, "meta_attribute", "<attribute_class>"),
    (919, "meta_attribute", "<attribute_content>"),
    (920, "meta_attribute", "<attribute_description>"),
    (921, "meta_attribute", "<attribute_lang>"),
    (922, "meta_attribute", "<attribute_scheme>"),
    (923, "meta_attribute", "<attribute_name>"),
    (924, "meta_attribute", "<attribute_href>"),
    (925, "meta_attributes", ("<meta_attribute> <meta_attribute> <meta_attribute> <meta_attribute> <meta_attribute>")),
    (927, "meter_attribute", "<attribute_disabled>"),
    (928, "meter_attribute", "<attribute_style>"),
    (929, "meter_attribute", "<attribute_contenteditable>"),
    (930, "meter_attribute", "<attribute_name>"),
    (931, "meter_attribute", "<attribute_min>"),
    (932, "meter_attribute", "<attribute_max>"),
    (933, "meter_attribute", "<attribute_class>"),
    (934, "meter_attribute", "<attribute_value>"),
    (935, "meter_attribute", "<attribute_high>"),
    (936, "meter_attribute", "<attribute_low>"),
    (937, "meter_attribute", "<attribute_optimum>"),
    (
        938,
        "meter_attributes",
        ("<meter_attribute> <meter_attribute> <meter_attribute> <meter_attribute> <meter_attribute>"),
    ),
    (940, "nav_attribute", "<attribute_name>"),
    (941, "nav_attribute", "<attribute_class>"),
    (942, "nav_attribute", "<attribute_role>"),
    (943, "nav_attributes", "<nav_attribute> <nav_attribute> <nav_attribute>"),
    (945, "nobr_attribute", "<attribute_style>"),
    (946, "nobr_attributes", "<nobr_attribute>"),
    (948, "noframes_attribute", "<attribute_lang>"),
    (949, "noframes_attribute", "<attribute_class>"),
    (950, "noframes_attribute", "<attribute_dir>"),
    (951, "noframes_attribute", "<attribute_title>"),
    (
        952,
        "noframes_attributes",
        ("<noframes_attribute> <noframes_attribute> <noframes_attribute> <noframes_attribute>"),
    ),
    (954, "noscript_attribute", "<attribute_lang>"),
    (955, "noscript_attribute", "<attribute_class>"),
    (956, "noscript_attribute", "<attribute_dir>"),
    (957, "noscript_attribute", "<attribute_title>"),
    (
        958,
        "noscript_attributes",
        ("<noscript_attribute> <noscript_attribute> <noscript_attribute> <noscript_attribute>"),
    ),
    (960, "object_attribute", "<attribute_classid>"),
    (961, "object_attribute", "<attribute_shouldfocus>"),
    (962, "object_attribute", "<attribute_onerror>"),
    (963, "object_attribute", "<attribute_usemap>"),
    (964, "object_attribute", "<attribute_itemprop>"),
    (965, "object_attribute", "<attribute_height>"),
    (966, "object_attribute", "<attribute_disabled>"),
    (967, "object_attribute", "<attribute_codetype>"),
    (968, "object_attribute", "<attribute_border>"),
    (969, "object_attribute", "<attribute_onload>"),
    (970, "object_attribute", "<attribute_style>"),
    (971, "object_attribute", "<attribute_hspace>"),
    (972, "object_attribute", "<attribute_archive>"),
    (973, "object_attribute", "<attribute_width>"),
    (974, "object_attribute", "<attribute_type>"),
    (975, "object_attribute", "<attribute_form>"),
    (976, "object_attribute", "<attribute_codebase>"),
    (977, "object_attribute", "<attribute_data>"),
    (978, "object_attribute", "<attribute_class>"),
    (979, "object_attribute", "<attribute_src>"),
    (980, "object_attribute", "<attribute_onunload>"),
    (981, "object_attribute", "<attribute_contenteditable>"),
    (982, "object_attribute", "<attribute_name>"),
    (983, "object_attribute", "<attribute_standby>"),
    (984, "object_attribute", "<attribute_align>"),
    (985, "object_attribute", "<attribute_draggable>"),
    (986, "object_attribute", "<attribute_vspace>"),
    (987, "object_attribute", "<attribute_declare>"),
    (988, "object_attribute", "<attribute_tabindex>"),
    (
        989,
        "object_attributes",
        ("<object_attribute> <object_attribute> <object_attribute> <object_attribute> <object_attribute>"),
    ),
    (991, "ol_attribute", "<attribute_style>"),
    (992, "ol_attribute", "<attribute_contenteditable>"),
    (993, "ol_attribute", "<attribute_name>"),
    (994, "ol_attribute", "<attribute_compact>"),
    (995, "ol_attribute", "<attribute_reversed>"),
    (996, "ol_attribute", "<attribute_oninput>"),
    (997, "ol_attribute", "<attribute_class>"),
    (998, "ol_attribute", "<attribute_start>"),
    (999, "ol_attribute", "<attribute_role>"),
    (1000, "ol_attribute", "<attribute_type>"),
    (1001, "ol_attribute", "<attribute_dir>"),
    (1002, "ol_attribute", "<attribute_tabindex>"),
    (1003, "ol_attributes", "<ol_attribute> <ol_attribute> <ol_attribute> <ol_attribute> <ol_attribute>"),
    (1005, "optgroup_attribute", "<attribute_style>"),
    (1006, "optgroup_attribute", "<attribute_label>"),
    (1007, "optgroup_attribute", "<attribute_disabled>"),
    (1008, "optgroup_attribute", "<attribute_dir>"),
    (1009, "optgroup_attribute", "<attribute_tabindex>"),
    (
        1010,
        "optgroup_attributes",
        ("<optgroup_attribute> <optgroup_attribute> <optgroup_attribute> <optgroup_attribute> <optgroup_attribute>"),
    ),
    (1012, "option_attribute", "<attribute_onerror>"),
    (1013, "option_attribute", "<attribute_accesskey>"),
    (1014, "option_attribute", "<attribute_onmousedown>"),
    (1015, "option_attribute", "<attribute_disabled>"),
    (1016, "option_attribute", "<attribute_style>"),
    (1017, "option_attribute", "<attribute_checked>"),
    (1018, "option_attribute", "<attribute_title>"),
    (1019, "option_attribute", "<attribute_selected>"),
    (1020, "option_attribute", "<attribute_label>"),
    (1021, "option_attribute", "<attribute_role>"),
    (1022, "option_attribute", "<attribute_onclick>"),
    (1023, "option_attribute", "<attribute_hidden>"),
    (1024, "option_attribute", "<attribute_onmouseup>"),
    (1025, "option_attribute", "<attribute_class>"),
    (1026, "option_attribute", "<attribute_name>"),
    (1027, "option_attribute", "<attribute_default>"),
    (1028, "option_attribute", "<attribute_value>"),
    (1029, "option_attribute", "<attribute_ondblclick>"),
    (1030, "option_attribute", "<attribute_dir>"),
    (1031, "option_attribute", "<attribute_tabindex>"),
    (
        1032,
        "option_attributes",
        ("<option_attribute> <option_attribute> <option_attribute> <option_attribute> <option_attribute>"),
    ),
    (1034, "output_attribute", "<attribute_style>"),
    (1035, "output_attribute", "<attribute_name>"),
    (1036, "output_attribute", "<attribute_for>"),
    (1037, "output_attribute", "<attribute_disabled>"),
    (1038, "output_attribute", "<attribute_form>"),
    (
        1039,
        "output_attributes",
        ("<output_attribute> <output_attribute> <output_attribute> <output_attribute> <output_attribute>"),
    ),
    (1041, "p_attribute", "<attribute_charset>"),
    (1042, "p_attribute", "<attribute_onerror>"),
    (1043, "p_attribute", "<attribute_style>"),
    (1044, "p_attribute", "<attribute_spellcheck>"),
    (1045, "p_attribute", "<attribute_title>"),
    (1046, "p_attribute", "<attribute_onclick>"),
    (1047, "p_attribute", "<attribute_hidden>"),
    (1048, "p_attribute", "<attribute_case>"),
    (1049, "p_attribute", "<attribute_class>"),
    (1050, "p_attribute", "<attribute_lang>"),
    (1051, "p_attribute", "<attribute_contenteditable>"),
    (1052, "p_attribute", "<attribute_name>"),
    (1053, "p_attribute", "<attribute_onfocus>"),
    (1054, "p_attribute", "<attribute_align>"),
    (1055, "p_attribute", "<attribute_margin>"),
    (1056, "p_attribute", "<attribute_dir>"),
    (1057, "p_attribute", "<attribute_tabindex>"),
    (1058, "p_attributes", "<p_attribute> <p_attribute> <p_attribute> <p_attribute> <p_attribute>"),
    (1060, "param_attribute", "<attribute_name>"),
    (1061, "param_attribute", "<attribute_valuetype>"),
    (1062, "param_attribute", "<attribute_value>"),
    (1063, "param_attribute", "<attribute_type>"),
    (1064, "param_attributes", "<param_attribute> <param_attribute> <param_attribute> <param_attribute>"),
    (1066, "pre_attribute", "<attribute_style>"),
    (1067, "pre_attribute", "<attribute_contenteditable>"),
    (1068, "pre_attribute", "<attribute_name>"),
    (1069, "pre_attribute", "<attribute_form>"),
    (1070, "pre_attribute", "<attribute_title>"),
    (1071, "pre_attribute", "<attribute_onerror>"),
    (1072, "pre_attribute", "<attribute_class>"),
    (1073, "pre_attribute", "<attribute_width>"),
    (1074, "pre_attribute", "<attribute_oncopy>"),
    (1075, "pre_attribute", "<attribute_wrap>"),
    (1076, "pre_attribute", "<attribute_dir>"),
    (1077, "pre_attribute", "<attribute_tabindex>"),
    (1078, "pre_attributes", ("<pre_attribute> <pre_attribute> <pre_attribute> <pre_attribute> <pre_attribute>")),
    (1080, "progress_attribute", "<attribute_style>"),
    (1081, "progress_attribute", "<attribute_name>"),
    (1082, "progress_attribute", "<attribute_min>"),
    (1083, "progress_attribute", "<attribute_max>"),
    (1084, "progress_attribute", "<attribute_indeterminate>"),
    (1085, "progress_attribute", "<attribute_value>"),
    (1086, "progress_attribute", "<attribute_class>"),
    (1087, "progress_attribute", "<attribute_disabled>"),
    (1088, "progress_attribute", "<attribute_role>"),
    (1089, "progress_attribute", "<attribute_hidden>"),
    (1090, "progress_attribute", "<attribute_dir>"),
    (
        1091,
        "progress_attributes",
        ("<progress_attribute> <progress_attribute> <progress_attribute> <progress_attribute> <progress_attribute>"),
    ),
    (1093, "q_attribute", "<attribute_lang>"),
    (1094, "q_attribute", "<attribute_style>"),
    (1095, "q_attribute", "<attribute_title>"),
    (1096, "q_attribute", "<attribute_class>"),
    (1097, "q_attribute", "<attribute_role>"),
    (1098, "q_attribute", "<attribute_cite>"),
    (1099, "q_attribute", "<attribute_tabindex>"),
    (1100, "q_attributes", "<q_attribute> <q_attribute> <q_attribute> <q_attribute> <q_attribute>"),
    (1102, "rp_attribute", "<attribute_name>"),
    (1103, "rp_attribute", "<attribute_class>"),
    (1104, "rp_attributes", "<rp_attribute> <rp_attribute>"),
    (1106, "rt_attribute", "<attribute_style>"),
    (1107, "rt_attribute", "<attribute_name>"),
    (1108, "rt_attribute", "<attribute_class>"),
    (1109, "rt_attribute", "<attribute_dir>"),
    (1110, "rt_attributes", "<rt_attribute> <rt_attribute> <rt_attribute> <rt_attribute>"),
    (1112, "ruby_attribute", "<attribute_lang>"),
    (1113, "ruby_attribute", "<attribute_style>"),
    (1114, "ruby_attribute", "<attribute_name>"),
    (1115, "ruby_attribute", "<attribute_class>"),
    (1116, "ruby_attribute", "<attribute_dir>"),
    (1117, "ruby_attributes", ("<ruby_attribute> <ruby_attribute> <ruby_attribute> <ruby_attribute> <ruby_attribute>")),
    (1119, "s_attribute", "<attribute_lang>"),
    (1120, "s_attribute", "<attribute_title>"),
    (1121, "s_attribute", "<attribute_class>"),
    (1122, "s_attribute", "<attribute_dir>"),
    (1123, "s_attribute", "<attribute_tabindex>"),
    (1124, "s_attributes", "<s_attribute> <s_attribute> <s_attribute> <s_attribute> <s_attribute>"),
    (1126, "samp_attribute", "<attribute_lang>"),
    (1127, "samp_attribute", "<attribute_title>"),
    (1128, "samp_attribute", "<attribute_class>"),
    (1129, "samp_attribute", "<attribute_dir>"),
    (1130, "samp_attribute", "<attribute_tabindex>"),
    (1131, "samp_attributes", ("<samp_attribute> <samp_attribute> <samp_attribute> <samp_attribute> <samp_attribute>")),
    (1133, "script_attribute", "<attribute_defer>"),
    (1134, "script_attribute", "<attribute_crossorigin>"),
    (1135, "script_attribute", "<attribute_onerror>"),
    (1136, "script_attribute", "<attribute_text>"),
    (1137, "script_attribute", "<attribute_onload>"),
    (1138, "script_attribute", "<attribute_style>"),
    (1139, "script_attribute", "<attribute_charset>"),
    (1140, "script_attribute", "<attribute_type>"),
    (1141, "script_attribute", "<attribute_nonce>"),
    (1142, "script_attribute", "<attribute_onbeforescriptexecute>"),
    (1143, "script_attribute", "<attribute_class>"),
    (1144, "script_attribute", "<attribute_src>"),
    (1145, "script_attribute", "<attribute_for>"),
    (1146, "script_attribute", "<attribute_language>"),
    (1147, "script_attribute", "<attribute_onafterscriptexecute>"),
    (1148, "script_attribute", "<attribute_async>"),
    (1149, "script_attribute", "<attribute_srcset>"),
    (
        1150,
        "script_attributes",
        ("<script_attribute> <script_attribute> <script_attribute> <script_attribute> <script_attribute>"),
    ),
    (1152, "section_attribute", "<attribute_style>"),
    (1153, "section_attribute", "<attribute_contenteditable>"),
    (1154, "section_attribute", "<attribute_name>"),
    (1155, "section_attribute", "<attribute_is>"),
    (1156, "section_attribute", "<attribute_class>"),
    (
        1157,
        "section_attributes",
        ("<section_attribute> <section_attribute> <section_attribute> <section_attribute> <section_attribute>"),
    ),
    (1159, "select_attribute", "<attribute_onerror>"),
    (1160, "select_attribute", "<attribute_accesskey>"),
    (1161, "select_attribute", "<attribute_onmousedown>"),
    (1162, "select_attribute", "<attribute_disabled>"),
    (1163, "select_attribute", "<attribute_onchange>"),
    (1164, "select_attribute", "<attribute_size>"),
    (1165, "select_attribute", "<attribute_style>"),
    (1166, "select_attribute", "<attribute_title>"),
    (1167, "select_attribute", "<attribute_readonly>"),
    (1168, "select_attribute", "<attribute_role>"),
    (1169, "select_attribute", "<attribute_onclick>"),
    (1170, "select_attribute", "<attribute_autofocus>"),
    (1171, "select_attribute", "<attribute_onblur>"),
    (1172, "select_attribute", "<attribute_multiple>"),
    (1173, "select_attribute", "<attribute_onmouseup>"),
    (1174, "select_attribute", "<attribute_form>"),
    (1175, "select_attribute", "<attribute_oninput>"),
    (1176, "select_attribute", "<attribute_class>"),
    (1177, "select_attribute", "<attribute_contenteditable>"),
    (1178, "select_attribute", "<attribute_name>"),
    (1179, "select_attribute", "<attribute_onfocus>"),
    (1180, "select_attribute", "<attribute_align>"),
    (1181, "select_attribute", "<attribute_required>"),
    (1182, "select_attribute", "<attribute_value>"),
    (1183, "select_attribute", "<attribute_ondblclick>"),
    (1184, "select_attribute", "<attribute_dir>"),
    (1185, "select_attribute", "<attribute_tabindex>"),
    (
        1186,
        "select_attributes",
        ("<select_attribute> <select_attribute> <select_attribute> <select_attribute> <select_attribute>"),
    ),
    (1188, "small_attribute", "<attribute_lang>"),
    (1189, "small_attribute", "<attribute_title>"),
    (1190, "small_attribute", "<attribute_class>"),
    (1191, "small_attribute", "<attribute_dir>"),
    (1192, "small_attribute", "<attribute_tabindex>"),
    (
        1193,
        "small_attributes",
        ("<small_attribute> <small_attribute> <small_attribute> <small_attribute> <small_attribute>"),
    ),
    (1195, "source_attribute", "<attribute_src>"),
    (1196, "source_attribute", "<attribute_style>"),
    (1197, "source_attribute", "<attribute_name>"),
    (1198, "source_attribute", "<attribute_sizes>"),
    (1199, "source_attribute", "<attribute_onerror>"),
    (1200, "source_attribute", "<attribute_media>"),
    (1201, "source_attribute", "<attribute_srcset>"),
    (1202, "source_attribute", "<attribute_type>"),
    (
        1203,
        "source_attributes",
        ("<source_attribute> <source_attribute> <source_attribute> <source_attribute> <source_attribute>"),
    ),
    (1205, "spacer_attribute", "<attribute_width>"),
    (1206, "spacer_attribute", "<attribute_style>"),
    (1207, "spacer_attribute", "<attribute_type>"),
    (1208, "spacer_attributes", "<spacer_attribute> <spacer_attribute> <spacer_attribute>"),
    (1210, "span_attribute", "<attribute_onerror>"),
    (1211, "span_attribute", "<attribute_disabled>"),
    (1212, "span_attribute", "<attribute_href>"),
    (1213, "span_attribute", "<attribute_alt>"),
    (1214, "span_attribute", "<attribute_slot>"),
    (1215, "span_attribute", "<attribute_style>"),
    (1216, "span_attribute", "<attribute_onmouseout>"),
    (1217, "span_attribute", "<attribute_form>"),
    (1218, "span_attribute", "<attribute_title>"),
    (1219, "span_attribute", "<attribute_width>"),
    (1220, "span_attribute", "<attribute_role>"),
    (1221, "span_attribute", "<attribute_onclick>"),
    (1222, "span_attribute", "<attribute_translate>"),
    (1223, "span_attribute", "<attribute_is>"),
    (1224, "span_attribute", "<attribute_ondragstart>"),
    (1225, "span_attribute", "<attribute_onmouseup>"),
    (1226, "span_attribute", "<attribute_formaction>"),
    (1227, "span_attribute", "<attribute_height>"),
    (1228, "span_attribute", "<attribute_class>"),
    (1229, "span_attribute", "<attribute_lang>"),
    (1230, "span_attribute", "<attribute_hidden>"),
    (1231, "span_attribute", "<attribute_contenteditable>"),
    (1232, "span_attribute", "<attribute_name>"),
    (1233, "span_attribute", "<attribute_onmouseover>"),
    (1234, "span_attribute", "<attribute_draggable>"),
    (1235, "span_attribute", "<attribute_dir>"),
    (1236, "span_attribute", "<attribute_tabindex>"),
    (1237, "span_attributes", ("<span_attribute> <span_attribute> <span_attribute> <span_attribute> <span_attribute>")),
    (1239, "strike_attribute", "<attribute_lang>"),
    (1240, "strike_attribute", "<attribute_style>"),
    (1241, "strike_attribute", "<attribute_title>"),
    (1242, "strike_attribute", "<attribute_class>"),
    (1243, "strike_attribute", "<attribute_dir>"),
    (1244, "strike_attribute", "<attribute_tabindex>"),
    (
        1245,
        "strike_attributes",
        ("<strike_attribute> <strike_attribute> <strike_attribute> <strike_attribute> <strike_attribute>"),
    ),
    (1247, "strong_attribute", "<attribute_lang>"),
    (1248, "strong_attribute", "<attribute_title>"),
    (1249, "strong_attribute", "<attribute_class>"),
    (1250, "strong_attribute", "<attribute_dir>"),
    (1251, "strong_attribute", "<attribute_tabindex>"),
    (
        1252,
        "strong_attributes",
        ("<strong_attribute> <strong_attribute> <strong_attribute> <strong_attribute> <strong_attribute>"),
    ),
    (1254, "style_attribute", "<attribute_nonce>"),
    (1255, "style_attribute", "<attribute_onload>"),
    (1256, "style_attribute", "<attribute_title>"),
    (1257, "style_attribute", "<attribute_onerror>"),
    (1258, "style_attribute", "<attribute_media>"),
    (1259, "style_attribute", "<attribute_dir>"),
    (1260, "style_attribute", "<attribute_scoped>"),
    (1261, "style_attribute", "<attribute_type>"),
    (
        1262,
        "style_attributes",
        ("<style_attribute> <style_attribute> <style_attribute> <style_attribute> <style_attribute>"),
    ),
    (1264, "sub_attribute", "<attribute_lang>"),
    (1265, "sub_attribute", "<attribute_style>"),
    (1266, "sub_attribute", "<attribute_contenteditable>"),
    (1267, "sub_attribute", "<attribute_title>"),
    (1268, "sub_attribute", "<attribute_class>"),
    (1269, "sub_attribute", "<attribute_dir>"),
    (1270, "sub_attribute", "<attribute_tabindex>"),
    (1271, "sub_attributes", ("<sub_attribute> <sub_attribute> <sub_attribute> <sub_attribute> <sub_attribute>")),
    (1273, "summary_attribute", "<attribute_style>"),
    (1274, "summary_attribute", "<attribute_title>"),
    (1275, "summary_attribute", "<attribute_class>"),
    (1276, "summary_attributes", "<summary_attribute> <summary_attribute> <summary_attribute>"),
    (1278, "sup_attribute", "<attribute_lang>"),
    (1279, "sup_attribute", "<attribute_style>"),
    (1280, "sup_attribute", "<attribute_title>"),
    (1281, "sup_attribute", "<attribute_class>"),
    (1282, "sup_attribute", "<attribute_dir>"),
    (1283, "sup_attribute", "<attribute_tabindex>"),
    (1284, "sup_attributes", ("<sup_attribute> <sup_attribute> <sup_attribute> <sup_attribute> <sup_attribute>")),
    (1286, "table_attribute", "<attribute_background>"),
    (1287, "table_attribute", "<attribute_frame>"),
    (1288, "table_attribute", "<attribute_cols>"),
    (1289, "table_attribute", "<attribute_height>"),
    (1290, "table_attribute", "<attribute_border>"),
    (1291, "table_attribute", "<attribute_style>"),
    (1292, "table_attribute", "<attribute_layout>"),
    (1293, "table_attribute", "<attribute_width>"),
    (1294, "table_attribute", "<attribute_hspace>"),
    (1295, "table_attribute", "<attribute_bgcolor>"),
    (1296, "table_attribute", "<attribute_role>"),
    (1297, "table_attribute", "<attribute_valign>"),
    (1298, "table_attribute", "<attribute_cellspacing>"),
    (1299, "table_attribute", "<attribute_bordercolor>"),
    (1300, "table_attribute", "<attribute_rules>"),
    (1301, "table_attribute", "<attribute_class>"),
    (1302, "table_attribute", "<attribute_lang>"),
    (1303, "table_attribute", "<attribute_contenteditable>"),
    (1304, "table_attribute", "<attribute_align>"),
    (1305, "table_attribute", "<attribute_summary>"),
    (1306, "table_attribute", "<attribute_vspace>"),
    (1307, "table_attribute", "<attribute_cellpadding>"),
    (1308, "table_attribute", "<attribute_dir>"),
    (1309, "table_attribute", "<attribute_tabindex>"),
    (
        1310,
        "table_attributes",
        ("<table_attribute> <table_attribute> <table_attribute> <table_attribute> <table_attribute>"),
    ),
    (1312, "tbody_attribute", "<attribute_bordercolor>"),
    (1313, "tbody_attribute", "<attribute_style>"),
    (1314, "tbody_attribute", "<attribute_title>"),
    (1315, "tbody_attribute", "<attribute_align>"),
    (1316, "tbody_attribute", "<attribute_class>"),
    (1317, "tbody_attribute", "<attribute_char>"),
    (1318, "tbody_attribute", "<attribute_bgcolor>"),
    (1319, "tbody_attribute", "<attribute_role>"),
    (1320, "tbody_attribute", "<attribute_valign>"),
    (1321, "tbody_attribute", "<attribute_charoff>"),
    (1322, "tbody_attribute", "<attribute_dir>"),
    (1323, "tbody_attribute", "<attribute_tabindex>"),
    (
        1324,
        "tbody_attributes",
        ("<tbody_attribute> <tbody_attribute> <tbody_attribute> <tbody_attribute> <tbody_attribute>"),
    ),
    (1326, "td_attribute", "<attribute_colspan>"),
    (1327, "td_attribute", "<attribute_height>"),
    (1328, "td_attribute", "<attribute_char>"),
    (1329, "td_attribute", "<attribute_nowrap>"),
    (1330, "td_attribute", "<attribute_border>"),
    (1331, "td_attribute", "<attribute_axis>"),
    (1332, "td_attribute", "<attribute_style>"),
    (1333, "td_attribute", "<attribute_rowspan>"),
    (1334, "td_attribute", "<attribute_width>"),
    (1335, "td_attribute", "<attribute_onmousemove>"),
    (1336, "td_attribute", "<attribute_bgcolor>"),
    (1337, "td_attribute", "<attribute_role>"),
    (1338, "td_attribute", "<attribute_valign>"),
    (1339, "td_attribute", "<attribute_scope>"),
    (1340, "td_attribute", "<attribute_hidden>"),
    (1341, "td_attribute", "<attribute_bordercolor>"),
    (1342, "td_attribute", "<attribute_abbr>"),
    (1343, "td_attribute", "<attribute_background>"),
    (1344, "td_attribute", "<attribute_class>"),
    (1345, "td_attribute", "<attribute_src>"),
    (1346, "td_attribute", "<attribute_contenteditable>"),
    (1347, "td_attribute", "<attribute_name>"),
    (1348, "td_attribute", "<attribute_charoff>"),
    (1349, "td_attribute", "<attribute_align>"),
    (1350, "td_attribute", "<attribute_headers>"),
    (1351, "td_attribute", "<attribute_dir>"),
    (1352, "td_attribute", "<attribute_tabindex>"),
    (1353, "td_attributes", "<td_attribute> <td_attribute> <td_attribute> <td_attribute> <td_attribute>"),
    (1355, "template_attribute", "<attribute_content>"),
    (1356, "template_attributes", "<template_attribute>"),
    (1358, "textarea_attribute", "<attribute_onselect>"),
    (1359, "textarea_attribute", "<attribute_onerror>"),
    (1360, "textarea_attribute", "<attribute_accesskey>"),
    (1361, "textarea_attribute", "<attribute_cols>"),
    (1362, "textarea_attribute", "<attribute_disabled>"),
    (1363, "textarea_attribute", "<attribute_wrap>"),
    (1364, "textarea_attribute", "<attribute_dirname>"),
    (1365, "textarea_attribute", "<attribute_row>"),
    (1366, "textarea_attribute", "<attribute_style>"),
    (1367, "textarea_attribute", "<attribute_rows>"),
    (1368, "textarea_attribute", "<attribute_spellcheck>"),
    (1369, "textarea_attribute", "<attribute_oninvalid>"),
    (1370, "textarea_attribute", "<attribute_width>"),
    (1371, "textarea_attribute", "<attribute_readonly>"),
    (1372, "textarea_attribute", "<attribute_role>"),
    (1373, "textarea_attribute", "<attribute_onclick>"),
    (1374, "textarea_attribute", "<attribute_autofocus>"),
    (1375, "textarea_attribute", "<attribute_type>"),
    (1376, "textarea_attribute", "<attribute_onblur>"),
    (1377, "textarea_attribute", "<attribute_placeholder>"),
    (1378, "textarea_attribute", "<attribute_class>"),
    (1379, "textarea_attribute", "<attribute_hidden>"),
    (1380, "textarea_attribute", "<attribute_onchange>"),
    (1381, "textarea_attribute", "<attribute_name>"),
    (1382, "textarea_attribute", "<attribute_onfocus>"),
    (1383, "textarea_attribute", "<attribute_align>"),
    (1384, "textarea_attribute", "<attribute_required>"),
    (1385, "textarea_attribute", "<attribute_value>"),
    (1386, "textarea_attribute", "<attribute_draggable>"),
    (1387, "textarea_attribute", "<attribute_oninput>"),
    (1388, "textarea_attribute", "<attribute_onkeydown>"),
    (1389, "textarea_attribute", "<attribute_maxlength>"),
    (1390, "textarea_attribute", "<attribute_onkeyup>"),
    (1391, "textarea_attribute", "<attribute_dir>"),
    (1392, "textarea_attribute", "<attribute_tabindex>"),
    (
        1393,
        "textarea_attributes",
        ("<textarea_attribute> <textarea_attribute> <textarea_attribute> <textarea_attribute> <textarea_attribute>"),
    ),
    (1395, "tfoot_attribute", "<attribute_style>"),
    (1396, "tfoot_attribute", "<attribute_align>"),
    (1397, "tfoot_attribute", "<attribute_class>"),
    (1398, "tfoot_attribute", "<attribute_char>"),
    (1399, "tfoot_attribute", "<attribute_valign>"),
    (1400, "tfoot_attribute", "<attribute_charoff>"),
    (1401, "tfoot_attribute", "<attribute_tabindex>"),
    (
        1402,
        "tfoot_attributes",
        ("<tfoot_attribute> <tfoot_attribute> <tfoot_attribute> <tfoot_attribute> <tfoot_attribute>"),
    ),
    (1404, "th_attribute", "<attribute_colspan>"),
    (1405, "th_attribute", "<attribute_height>"),
    (1406, "th_attribute", "<attribute_char>"),
    (1407, "th_attribute", "<attribute_nowrap>"),
    (1408, "th_attribute", "<attribute_axis>"),
    (1409, "th_attribute", "<attribute_style>"),
    (1410, "th_attribute", "<attribute_rowspan>"),
    (1411, "th_attribute", "<attribute_width>"),
    (1412, "th_attribute", "<attribute_bgcolor>"),
    (1413, "th_attribute", "<attribute_valign>"),
    (1414, "th_attribute", "<attribute_scope>"),
    (1415, "th_attribute", "<attribute_hidden>"),
    (1416, "th_attribute", "<attribute_charoff>"),
    (1417, "th_attribute", "<attribute_abbr>"),
    (1418, "th_attribute", "<attribute_background>"),
    (1419, "th_attribute", "<attribute_class>"),
    (1420, "th_attribute", "<attribute_align>"),
    (1421, "th_attribute", "<attribute_headers>"),
    (1422, "th_attribute", "<attribute_tabindex>"),
    (1423, "th_attributes", "<th_attribute> <th_attribute> <th_attribute> <th_attribute> <th_attribute>"),
    (1425, "thead_attribute", "<attribute_style>"),
    (1426, "thead_attribute", "<attribute_align>"),
    (1427, "thead_attribute", "<attribute_class>"),
    (1428, "thead_attribute", "<attribute_char>"),
    (1429, "thead_attribute", "<attribute_onclick>"),
    (1430, "thead_attribute", "<attribute_valign>"),
    (1431, "thead_attribute", "<attribute_charoff>"),
    (1432, "thead_attribute", "<attribute_tabindex>"),
    (
        1433,
        "thead_attributes",
        ("<thead_attribute> <thead_attribute> <thead_attribute> <thead_attribute> <thead_attribute>"),
    ),
    (1435, "time_attribute", "<attribute_name>"),
    (1436, "time_attribute", "<attribute_datetime>"),
    (1437, "time_attributes", "<time_attribute> <time_attribute>"),
    (1439, "title_attribute", "<attribute_style>"),
    (1440, "title_attribute", "<attribute_class>"),
    (1441, "title_attributes", "<title_attribute> <title_attribute>"),
    (1443, "tr_attribute", "<attribute_bordercolor>"),
    (1444, "tr_attribute", "<attribute_style>"),
    (1445, "tr_attribute", "<attribute_align>"),
    (1446, "tr_attribute", "<attribute_class>"),
    (1447, "tr_attribute", "<attribute_char>"),
    (1448, "tr_attribute", "<attribute_bgcolor>"),
    (1449, "tr_attribute", "<attribute_role>"),
    (1450, "tr_attribute", "<attribute_background>"),
    (1451, "tr_attribute", "<attribute_valign>"),
    (1452, "tr_attribute", "<attribute_height>"),
    (1453, "tr_attribute", "<attribute_charoff>"),
    (1454, "tr_attribute", "<attribute_dir>"),
    (1455, "tr_attribute", "<attribute_tabindex>"),
    (1456, "tr_attributes", "<tr_attribute> <tr_attribute> <tr_attribute> <tr_attribute> <tr_attribute>"),
    (1458, "track_attribute", "<attribute_onload>"),
    (1459, "track_attribute", "<attribute_kind>"),
    (1460, "track_attribute", "<attribute_src>"),
    (1461, "track_attribute", "<attribute_onerror>"),
    (1462, "track_attribute", "<attribute_label>"),
    (1463, "track_attribute", "<attribute_default>"),
    (1464, "track_attribute", "<attribute_mode>"),
    (1465, "track_attribute", "<attribute_srclang>"),
    (
        1466,
        "track_attributes",
        ("<track_attribute> <track_attribute> <track_attribute> <track_attribute> <track_attribute>"),
    ),
    (1468, "tt_attribute", "<attribute_lang>"),
    (1469, "tt_attribute", "<attribute_style>"),
    (1470, "tt_attribute", "<attribute_title>"),
    (1471, "tt_attribute", "<attribute_class>"),
    (1472, "tt_attribute", "<attribute_dir>"),
    (1473, "tt_attribute", "<attribute_tabindex>"),
    (1474, "tt_attributes", "<tt_attribute> <tt_attribute> <tt_attribute> <tt_attribute> <tt_attribute>"),
    (1476, "u_attribute", "<attribute_lang>"),
    (1477, "u_attribute", "<attribute_style>"),
    (1478, "u_attribute", "<attribute_title>"),
    (1479, "u_attribute", "<attribute_class>"),
    (1480, "u_attribute", "<attribute_dir>"),
    (1481, "u_attribute", "<attribute_tabindex>"),
    (1482, "u_attributes", "<u_attribute> <u_attribute> <u_attribute> <u_attribute> <u_attribute>"),
    (1484, "ul_attribute", "<attribute_style>"),
    (1485, "ul_attribute", "<attribute_contenteditable>"),
    (1486, "ul_attribute", "<attribute_compact>"),
    (1487, "ul_attribute", "<attribute_class>"),
    (1488, "ul_attribute", "<attribute_role>"),
    (1489, "ul_attribute", "<attribute_onclick>"),
    (1490, "ul_attribute", "<attribute_type>"),
    (1491, "ul_attribute", "<attribute_dir>"),
    (1492, "ul_attribute", "<attribute_tabindex>"),
    (1493, "ul_attributes", "<ul_attribute> <ul_attribute> <ul_attribute> <ul_attribute> <ul_attribute>"),
    (1495, "var_attribute", "<attribute_lang>"),
    (1496, "var_attribute", "<attribute_style>"),
    (1497, "var_attribute", "<attribute_title>"),
    (1498, "var_attribute", "<attribute_class>"),
    (1499, "var_attribute", "<attribute_dir>"),
    (1500, "var_attribute", "<attribute_tabindex>"),
    (1501, "var_attributes", ("<var_attribute> <var_attribute> <var_attribute> <var_attribute> <var_attribute>")),
    (1503, "video_attribute", "<attribute_onloadeddata>"),
    (1504, "video_attribute", "<attribute_crossorigin>"),
    (1505, "video_attribute", "<attribute_onerror>"),
    (1506, "video_attribute", "<attribute_autoload>"),
    (1507, "video_attribute", "<attribute_height>"),
    (1508, "video_attribute", "<attribute_onloadstart>"),
    (1509, "video_attribute", "<attribute_style>"),
    (1510, "video_attribute", "<attribute_title>"),
    (1511, "video_attribute", "<attribute_controls>"),
    (1512, "video_attribute", "<attribute_width>"),
    (1513, "video_attribute", "<attribute_hidden>"),
    (1514, "video_attribute", "<attribute_preload>"),
    (1515, "video_attribute", "<attribute_poster>"),
    (1516, "video_attribute", "<attribute_class>"),
    (1517, "video_attribute", "<attribute_videosrc>"),
    (1518, "video_attribute", "<attribute_onloadedmetadata>"),
    (1519, "video_attribute", "<attribute_name>"),
    (1520, "video_attribute", "<attribute_onfocus>"),
    (1521, "video_attribute", "<attribute_ontimeupdate>"),
    (1522, "video_attribute", "<attribute_oncanplaythrough>"),
    (1523, "video_attribute", "<attribute_draggable>"),
    (1524, "video_attribute", "<attribute_allowfullscreen>"),
    (1525, "video_attribute", "<attribute_onplaying>"),
    (1526, "video_attribute", "<attribute_autoplay>"),
    (1527, "video_attribute", "<attribute_onseeked>"),
    (1528, "video_attribute", "<attribute_loop>"),
    (1529, "video_attribute", "<attribute_tabindex>"),
    (
        1530,
        "video_attributes",
        ("<video_attribute> <video_attribute> <video_attribute> <video_attribute> <video_attribute>"),
    ),
    (1532, "wbr_attribute", "<attribute_style>"),
    (1533, "wbr_attributes", "<wbr_attribute>"),
    (1535, "xmp_attribute", "<attribute_class>"),
    (1536, "xmp_attributes", "<xmp_attribute>"),
)

COMMON_RULES: Final[tuple[TableRule, ...]] = (
    (15, "newline", "<cr><lf>"),
    (17, "interestingint", "32768"),
    (18, "interestingint", "65535"),
    (19, "interestingint", "65536"),
    (20, "interestingint", "1073741824"),
    (21, "interestingint", "536870912"),
    (22, "interestingint", "268435456"),
    (23, "interestingint", "4294967295"),
    (24, "interestingint", "2147483648"),
    (25, "interestingint", "2147483647"),
    (26, "interestingint", "-2147483648"),
    (27, "interestingint", "-1073741824"),
    (28, "interestingint", "-32769"),
    (30, "fuzzint", "0"),
    (31, "fuzzint", "0"),
    (32, "fuzzint", "0"),
    (33, "fuzzint", "1"),
    (34, "fuzzint", "1"),
    (35, "fuzzint", "-1"),
    (36, "fuzzint", "<int min=0 max=10>"),
    (37, "fuzzint", "<int min=0 max=100>"),
    (38, "short", "<interestingint>"),
    (40, "boolean", "true"),
    (41, "boolean", "false"),
    (43, "percentage", "<int min=0 max=100>"),
    (45, "elementid", "htmlvar0000<int min=1 max=9>"),
    (46, "svgelementid", "svgvar0000<int min=1 max=9>"),
    (47, "mathmlelementid", "mathmlvar0000<int min=1 max=9>"),
    (48, "class", "class<int min=0 max=9>"),
    (50, "color", "red"),
    (51, "color", "green"),
    (52, "color", "white"),
    (53, "color", "black"),
    (54, "color", "#<hex><hex><hex><hex><hex><hex>"),
    (55, "color", "rgb(<int min=0 max=255>,<int min=0 max=255>,<int min=0 max=255>)"),
    (57, "tagname", "a"),
    (58, "tagname", "abbr"),
    (59, "tagname", "acronym"),
    (60, "tagname", "address"),
    (61, "tagname", "applet"),
    (62, "tagname", "area"),
    (63, "tagname", "article"),
    (64, "tagname", "aside"),
    (65, "tagname", "audio"),
    (66, "tagname", "b"),
    (67, "tagname", "base"),
    (68, "tagname", "basefont"),
    (69, "tagname", "bdi"),
    (70, "tagname", "bdo"),
    (71, "tagname", "bgsound"),
    (72, "tagname", "big"),
    (73, "tagname", "blink"),
    (74, "tagname", "blockquote"),
    (75, "tagname", "body"),
    (76, "tagname", "br"),
    (77, "tagname", "button"),
    (78, "tagname", "canvas"),
    (79, "tagname", "caption"),
    (80, "tagname", "center"),
    (81, "tagname", "cite"),
    (82, "tagname", "code"),
    (83, "tagname", "col"),
    (84, "tagname", "colgroup"),
    (85, "tagname", "command"),
    (86, "tagname", "content"),
    (87, "tagname", "data"),
    (88, "tagname", "datalist"),
    (89, "tagname", "dd"),
    (90, "tagname", "del"),
    (91, "tagname", "details"),
    (92, "tagname", "dfn"),
    (93, "tagname", "dialog"),
    (94, "tagname", "dir"),
    (95, "tagname", "div"),
    (96, "tagname", "dl"),
    (97, "tagname", "dt"),
    (98, "tagname", "element"),
    (99, "tagname", "em"),
    (100, "tagname", "embed"),
    (101, "tagname", "fieldset"),
    (102, "tagname", "figcaption"),
    (103, "tagname", "figure"),
    (104, "tagname", "font"),
    (105, "tagname", "footer"),
    (106, "tagname", "form"),
    (107, "tagname", "frame"),
    (108, "tagname", "frameset"),
    (109, "tagname", "h1"),
    (110, "tagname", "h2"),
    (111, "tagname", "h3"),
    (112, "tagname", "h4"),
    (113, "tagname", "h5"),
    (114, "tagname", "h6"),
    (115, "tagname", "head"),
    (116, "tagname", "header"),
    (117, "tagname", "hgroup"),
    (118, "tagname", "hr"),
    (119, "tagname", "html"),
    (120, "tagname", "i"),
    (121, "tagname", "iframe"),
    (122, "tagname", "image"),
    (123, "tagname", "img"),
    (124, "tagname", "input"),
    (125, "tagname", "ins"),
    (126, "tagname", "isindex"),
    (127, "tagname", "kbd"),
    (128, "tagname", "keygen"),
    (129, "tagname", "label"),
    (130, "tagname", "layer"),
    (131, "tagname", "legend"),
    (132, "tagname", "li"),
    (133, "tagname", "link"),
    (134, "tagname", "listing"),
    (135, "tagname", "main"),
    (136, "tagname", "map"),
    (137, "tagname", "mark"),
    (138, "tagname", "marquee"),
    (139, "tagname", "menu"),
    (140, "tagname", "menuitem"),
    (141, "tagname", "meta"),
    (142, "tagname", "meter"),
    (143, "tagname", "multicol"),
    (144, "tagname", "nav"),
    (145, "tagname", "nobr"),
    (146, "tagname", "noembed"),
    (147, "tagname", "noframes"),
    (148, "tagname", "nolayer"),
    (149, "tagname", "noscript"),
    (150, "tagname", "object"),
    (151, "tagname", "ol"),
    (152, "tagname", "optgroup"),
    (153, "tagname", "option"),
    (154, "tagname", "output"),
    (155, "tagname", "p"),
    (156, "tagname", "param"),
    (157, "tagname", "picture"),
    (158, "tagname", "plaintext"),
    (159, "tagname", "pre"),
    (160, "tagname", "progress"),
    (161, "tagname", "q"),
    (162, "tagname", "rp"),
    (163, "tagname", "rt"),
    (164, "tagname", "rtc"),
    (165, "tagname", "ruby"),
    (166, "tagname", "s"),
    (167, "tagname", "samp"),
    (168, "tagname", "script"),
    (169, "tagname", "section"),
    (170, "tagname", "select"),
    (171, "tagname", "shadow"),
    (172, "tagname", "small"),
    (173, "tagname", "source"),
    (174, "tagname", "spacer"),
    (175, "tagname", "span"),
    (176, "tagname", "strike"),
    (177, "tagname", "strong"),
    (178, "tagname", "style"),
    (179, "tagname", "sub"),
    (180, "tagname", "summary"),
    (181, "tagname", "sup"),
    (182, "tagname", "table"),
    (183, "tagname", "tbody"),
    (184, "tagname", "td"),
    (185, "tagname", "template"),
    (186, "tagname", "textarea"),
    (187, "tagname", "tfoot"),
    (188, "tagname", "th"),
    (189, "tagname", "thead"),
    (190, "tagname", "time"),
    (191, "tagname", "title"),
    (192, "tagname", "tr"),
    (193, "tagname", "track"),
    (194, "tagname", "tt"),
    (195, "tagname", "u"),
    (196, "tagname", "ul"),
    (197, "tagname", "var"),
    (198, "tagname", "video"),
    (199, "tagname", "wbr"),
    (200, "tagname", "xmp"),
    (202, "svgtagname", "a"),
    (203, "svgtagname", "altGlyph"),
    (204, "svgtagname", "altGlyphDef"),
    (205, "svgtagname", "altGlyphItem"),
    (206, "svgtagname", "animate"),
    (207, "svgtagname", "animateColor"),
    (208, "svgtagname", "animateMotion"),
    (209, "svgtagname", "animateTransform"),
    (210, "svgtagname", "circle"),
    (211, "svgtagname", "clipPath"),
    (212, "svgtagname", "cursor"),
    (213, "svgtagname", "defs"),
    (214, "svgtagname", "desc"),
    (215, "svgtagname", "ellipse"),
    (216, "svgtagname", "feBlend"),
    (217, "svgtagname", "feColorMatrix"),
    (218, "svgtagname", "feComponentTransfer"),
    (219, "svgtagname", "feComposite"),
    (220, "svgtagname", "feConvolveMatrix"),
    (221, "svgtagname", "feDiffuseLighting"),
    (222, "svgtagname", "feDisplacementMap"),
    (223, "svgtagname", "feDistantLight"),
    (224, "svgtagname", "feDropShadow"),
    (225, "svgtagname", "feFlood"),
    (226, "svgtagname", "feFuncA"),
    (227, "svgtagname", "feFuncB"),
    (228, "svgtagname", "feFuncG"),
    (229, "svgtagname", "feFuncR"),
    (230, "svgtagname", "feGaussianBlur"),
    (231, "svgtagname", "feImage"),
    (232, "svgtagname", "feMerge"),
    (233, "svgtagname", "feMergeNode"),
    (234, "svgtagname", "feMorphology"),
    (235, "svgtagname", "feOffset"),
    (236, "svgtagname", "fePointLight"),
    (237, "svgtagname", "feSpecularLighting"),
    (238, "svgtagname", "feSpotLight"),
    (239, "svgtagname", "feTile"),
    (240, "svgtagname", "feTurbulence"),
    (241, "svgtagname", "filter"),
    (242, "svgtagname", "font"),
    (243, "svgtagname", "font_face"),
    (244, "svgtagname", "font_face_format"),
    (245, "svgtagname", "font_face_name"),
    (246, "svgtagname", "font_face_src"),
    (247, "svgtagname", "font_face_uri"),
    (248, "svgtagname", "foreignObject"),
    (249, "svgtagname", "g"),
    (250, "svgtagname", "glyph"),
    (251, "svgtagname", "glyphRef"),
    (252, "svgtagname", "hkern"),
    (253, "svgtagname", "image"),
    (254, "svgtagname", "line"),
    (255, "svgtagname", "linearGradient"),
    (256, "svgtagname", "marker"),
    (257, "svgtagname", "mask"),
    (258, "svgtagname", "metadata"),
    (259, "svgtagname", "missing_glyph"),
    (260, "svgtagname", "mpath"),
    (261, "svgtagname", "path"),
    (262, "svgtagname", "pattern"),
    (263, "svgtagname", "polygon"),
    (264, "svgtagname", "polyline"),
    (265, "svgtagname", "radialGradient"),
    (266, "svgtagname", "rect"),
    (267, "svgtagname", "script"),
    (268, "svgtagname", "set"),
    (269, "svgtagname", "stop"),
    (270, "svgtagname", "style"),
    (271, "svgtagname", "svg"),
    (272, "svgtagname", "switch"),
    (273, "svgtagname", "symbol"),
    (274, "svgtagname", "text"),
    (275, "svgtagname", "textPath"),
    (276, "svgtagname", "title"),
    (277, "svgtagname", "tref"),
    (278, "svgtagname", "tspan"),
    (279, "svgtagname", "use"),
    (280, "svgtagname", "view"),
    (281, "svgtagname", "vkern"),
    (283, "mathmltagname", "annotation"),
    (284, "mathmltagname", "annotation-xml"),
    (285, "mathmltagname", "maction"),
    (286, "mathmltagname", "math"),
    (287, "mathmltagname", "merror"),
    (288, "mathmltagname", "mfrac"),
    (289, "mathmltagname", "mi"),
    (290, "mathmltagname", "mmultiscripts"),
    (291, "mathmltagname", "mn"),
    (292, "mathmltagname", "mo"),
    (293, "mathmltagname", "mover"),
    (294, "mathmltagname", "mpadded"),
    (295, "mathmltagname", "mphantom"),
    (296, "mathmltagname", "mprescripts"),
    (297, "mathmltagname", "mroot"),
    (298, "mathmltagname", "mrow"),
    (299, "mathmltagname", "ms"),
    (300, "mathmltagname", "mspace"),
    (301, "mathmltagname", "msqrt"),
    (302, "mathmltagname", "mstyle"),
    (303, "mathmltagname", "msub"),
    (304, "mathmltagname", "msubsup"),
    (305, "mathmltagname", "msup"),
    (306, "mathmltagname", "mtable"),
    (307, "mathmltagname", "mtd"),
    (308, "mathmltagname", "mtext"),
    (309, "mathmltagname", "mtr"),
    (310, "mathmltagname", "munder"),
    (311, "mathmltagname", "munderover"),
    (312, "mathmltagname", "none"),
    (313, "mathmltagname", "semantics"),
    (316, "cssurl", "<hash><elementid>"),
    (317, "cssurl", "<hash><svgelementid>"),
    (318, "cssurl", "<hash><mathmlelementid>"),
    (
        319,
        "cssurl",
        (
            "data:image/gif;base64,R0lGODlhEAAQAMQAAO"
            "RHHOVSKudfOulrSOp3WOyDZu6QdvCchPGolfO0o/"
            "XBs/fNwfjZ0frl3/zy7////wAAAAAAAAAAAAAAAA"
            "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
            "AAAAAAACH5BAkAABAALAAAAAAQABAAAAVVICSOZG"
            "lCQAosJ6mu7fiyZeKqNKToQGDsM8hBADgUXoGAiq"
            "hSvp5QAnQKGIgUhwFUYLCVDFCrKUE1lBavAViFID"
            "lTImbKC5Gm2hB0SlBCBMQiB0UjIQA7"
        ),
    ),
    (321, "imgsrc", "x"),
    (
        322,
        "imgsrc",
        (
            "data:image/gif;base64,R0lGODlhIAAgAPIBAG"
            "bMzP///wAAADOZZpn/zAAAAAAAAAAAACH5BAAAAA"
            "AALAAAAAAgACAAAAOLGLrc/k7ISau9S5DNu/8fIC"
            "gaYJ5oqqbDGJRrLAMtScw468J5Xr+3nm8XFM5+PG"
            "MMWYwxcMyZ40iULQaDhSzqDGBNisGyuhUDrmNb72"
            "pWcaXhtpsM/27pVi8UX96rcQpDf3V+QD12d4NKK2"
            "+Lc4qOKI2RJ5OUNHyXSDRYnZ6foKAuLxelphMQqa"
            "oPCQA7"
        ),
    ),
    (324, "videosrc", "x"),
    (
        325,
        "videosrc",
        (
            "data:video/mp4;base64,AAAAIGZ0eXBpc29tAA"
            "ACAGlzb21pc28yYXZjMW1wNDEAAAAIZnJlZQAAA5"
            "NtZGF0AAACrgYF//+q3EXpvebZSLeWLNgg2SPu73"
            "gyNjQgLSBjb3JlIDE0OCByMjY0MyA1YzY1NzA0IC"
            "0gSC4yNjQvTVBFRy00IEFWQyBjb2RlYyAtIENvcH"
            "lsZWZ0IDIwMDMtMjAxNSAtIGh0dHA6Ly93d3cudm"
            "lkZW9sYW4ub3JnL3gyNjQuaHRtbCAtIG9wdGlvbn"
            "M6IGNhYmFjPTEgcmVmPTMgZGVibG9jaz0xOjA6MC"
            "BhbmFseXNlPTB4MzoweDExMyBtZT1oZXggc3VibW"
            "U9NyBwc3k9MSBwc3lfcmQ9MS4wMDowLjAwIG1peG"
            "VkX3JlZj0xIG1lX3JhbmdlPTE2IGNocm9tYV9tZT"
            "0xIHRyZWxsaXM9MSA4eDhkY3Q9MSBjcW09MCBkZW"
            "Fkem9uZT0yMSwxMSBmYXN0X3Bza2lwPTEgY2hyb2"
            "1hX3FwX29mZnNldD0tMiB0aHJlYWRzPTEgbG9va2"
            "FoZWFkX3RocmVhZHM9MSBzbGljZWRfdGhyZWFkcz"
            "0wIG5yPTAgZGVjaW1hdGU9MSBpbnRlcmxhY2VkPT"
            "AgYmx1cmF5X2NvbXBhdD0wIGNvbnN0cmFpbmVkX2"
            "ludHJhPTAgYmZyYW1lcz0zIGJfcHlyYW1pZD0yIG"
            "JfYWRhcHQ9MSBiX2JpYXM9MCBkaXJlY3Q9MSB3ZW"
            "lnaHRiPTEgb3Blbl9nb3A9MCB3ZWlnaHRwPTIga2"
            "V5aW50PTI1MCBrZXlpbnRfbWluPTI1IHNjZW5lY3"
            "V0PTQwIGludHJhX3JlZnJlc2g9MCByY19sb29rYW"
            "hlYWQ9NDAgcmM9Y3JmIG1idHJlZT0xIGNyZj0yMy"
            "4wIHFjb21wPTAuNjAgcXBtaW49MCBxcG1heD02OS"
            "BxcHN0ZXA9NCBpcF9yYXRpbz0xLjQwIGFxPTE6MS"
            "4wMACAAAAAvWWIhAAh/9PWYQ7q+jvvWOfBgvpv0e"
            "IYkqWiQW6SsLQx8ByoouBLEC9HBQTAXOJh/wFnte"
            "OP+NH5Er2DeHrP4kxvjj4nXKG9Zm/FycSAdlzoMD"
            "OFc4CmXmCL51Dj+zekurxKazOLwXVd7f/rOQpa9+"
            "iPXYTZsRw+WFFNokI8saLT7Mt03UvGxwdAYkwe7U"
            "mwPZacue5goP6rQhBgGMjgK21nSHZWUcz5Y6Ec/w"
            "dCPp0Sxx/h6UsSneF9hINuvwAAAAhBmiJsQx92QA"
            "AAAAgBnkF5DH/EgQAAAzRtb292AAAAbG12aGQAAA"
            "AAAAAAAAAAAAAAAAPoAAAAZAABAAABAAAAAAAAAA"
            "AAAAAAAQAAAAAAAAAAAAAAAAAAAAEAAAAAAAAAAA"
            "AAAAAAAEAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
            "AAAAAAAAACAAACXnRyYWsAAABcdGtoZAAAAAMAAA"
            "AAAAAAAAAAAAEAAAAAAAAAZAAAAAAAAAAAAAAAAA"
            "AAAAAAAQAAAAAAAAAAAAAAAAAAAAEAAAAAAAAAAA"
            "AAAAAAAEAAAAAAIAAAACAAAAAAACRlZHRzAAAAHG"
            "Vsc3QAAAAAAAAAAQAAAGQAAAQAAAEAAAAAAdZtZG"
            "lhAAAAIG1kaGQAAAAAAAAAAAAAAAAAADwAAAAGAF"
            "XEAAAAAAAtaGRscgAAAAAAAAAAdmlkZQAAAAAAAA"
            "AAAAAAAFZpZGVvSGFuZGxlcgAAAAGBbWluZgAAAB"
            "R2bWhkAAAAAQAAAAAAAAAAAAAAJGRpbmYAAAAcZH"
            "JlZgAAAAAAAAABAAAADHVybCAAAAABAAABQXN0Ym"
            "wAAACVc3RzZAAAAAAAAAABAAAAhWF2YzEAAAAAAA"
            "AAAQAAAAAAAAAAAAAAAAAAAAAAIAAgAEgAAABIAA"
            "AAAAAAAAEAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
            "AAAAAAAAAAAAAY//8AAAAvYXZjQwFkAAr/4QAWZ2"
            "QACqzZSWhAAAADAEAAAA8DxIllgAEABmjr48siwA"
            "AAABhzdHRzAAAAAAAAAAEAAAADAAACAAAAABRzdH"
            "NzAAAAAAAAAAEAAAABAAAAKGN0dHMAAAAAAAAAAw"
            "AAAAEAAAQAAAAAAQAABgAAAAABAAACAAAAABxzdH"
            "NjAAAAAAAAAAEAAAABAAAAAwAAAAEAAAAgc3Rzeg"
            "AAAAAAAAAAAAAAAwAAA3MAAAAMAAAADAAAABRzdG"
            "NvAAAAAAAAAAEAAAAwAAAAYnVkdGEAAABabWV0YQ"
            "AAAAAAAAAhaGRscgAAAAAAAAAAbWRpcmFwcGwAAA"
            "AAAAAAAAAAAAAtaWxzdAAAACWpdG9vAAAAHWRhdG"
            "EAAAABAAAAAExhdmY1Ni40MC4xMDE="
        ),
    ),
    (327, "audiosrc", "x"),
    (
        328,
        "audiosrc",
        (
            "data:audio/mp3;base64,//uQxAAAAAAAAAAAAA"
            "AAAAAAAAAASW5mbwAAAA8AAAADAAAGhgBVVVVVVV"
            "VVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVWqqq"
            "qqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqq"
            "r///////////////////////////////////////"
            "////8AAAA5TEFNRTMuOTlyAc0AAAAAAAAAABSAJA"
            "KjQgAAgAAABoaLLYLcAAAAAAAAAAAAAAAAAAAAAA"
            "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
            "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
            "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
            "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
            "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
            "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
            "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
            "AAAAAAAAAAAAAAAAAA//uQxAAAVHoO86Ch/wKrQh"
            "+UIz/YShKDZqEIAAE3kQFg+NSyUDm5f/yB+D/GP8"
            "hjmzG6Jy7lvFu8Iif7i7vApIeVfN/DkGIKGInCaJ"
            "xNu9wifzeiTfJlaJX/Np//9wKClWWDcG4vBiIYwc"
            "B4NHigohguDcBcIxSiAaB4JAgT6jf2YDkQi5/mma"
            "bkya6nTRBy5uRyKB48TiFogeguDih66JwykEQBKz"
            "jbzTdl3FjUCgfnYZFWM01W3xx4g/qtMn//v/////"
            "9+j9oeZe+G35O3ZKZ9f+8N1LCTyD5/hhewsfDj0T"
            "DUzpMMkhzaPS6TS172Po89nnJ1mln9/pod31/j4j"
            "YgPWx7Aq5MUFns3tUmlSzP2fSvZYbOVT9OP3yLJ4"
            "kTEQacS6PSzeXtGQ2It0A5GhIiGn0WMgS8ajcLgZ"
            "5bBbhuIFSj0FuHwJQsY9yIPgmZ0C5kpLKpyAaBMi"
            "OBSC9Lmcypf2WJKVNItoAE2UDUo2XGvl3+5Sn5//"
            "/efkKpqSl6nNZq7mRvk4LTEpFJ8EAuIIcxAhRdGe"
            "jHgAcDIOpMMVju//uSxB6AVKYRAYCN/sKXwiAoFL"
            "/gDcjA/qGXMzOkX/l6QcZi6hvb6Y4WczOL93Ankf"
            "Jl7CVqfnbUQ0Ho3KpwmVbcT59DQkvrEhSnUC6Vj6"
            "U8DvLevkCV5hs+WMupZKsylEjyvcT0cEcY7S2P0Y"
            "SlVGAubM6oKYf5cj6jZk1KwsxdIeZzRc/S4vzv5e"
            "R9ur/9Leh0fZPPeV5uvbrzTv1SuTy5NxTyW3CF0v"
            "rF1tLFsuFa7336yxlTi7cnKcof3kvPKu5/1fyqy/"
            "lVf2b1DpDDpE7RIhSOJDZQicyQqsmKYEpKJ2M6Ib"
            "chCvO84TjUCHIWP411MmlAd6cVrAhDUf5xJU/mJk"
            "JihqdI4dY9D5RrxBi+sQeEacRPSTBouAj48i+Lh0"
            "4Z/8v/mf/f////+8V7RiRllObiOvpaJWu06xcyGP"
            "0pkpaptJDnnhj0eWiixyiewi5rebgxesayRHMuP+"
            "27WN/HfdbJvEP4fQXk7++VdHVMZm+0Oe2aU4o1xH"
            "Q5iSKepDeM60sIchLEqmFqep1TE9OEwxKtsdOtj1"
            "EFMyJsxcoWMv/7ksQ/gFTqEPwAmf7CYEId8BM/4J"
            "pLqWw6TTWAcxNS6msRk0RbhJT6D+FfP4lBBVSsgO"
            "JvhmkkOEjSBhUgSJQIpiTyc1V/nL+i/8UK//upf/"
            "4Sf9vjfy8+nynnTUTkjVVv7VZGEnfN9PLHSckai1"
            "d/TotT5X/9PLV2rznavW+ZYltU8yxyRqTkUTkjca"
            "TlgpiU0XVgsUcmATAkqN8xYUZh3lOsCilexWJqjv"
            "Xq8hR+qluTrIW5pOUyTCLESFHH6dLVGP5Li2qxlP"
            "1UD1JclJkro0lDNtVMQU1FMy45OS41VVVVVVVVVV"
            "VVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVV"
            "VVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVV"
            "VVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVV"
            "VVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVV"
            "VVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVV"
            "VVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVV"
            "VVVVVVVVU="
        ),
    ),
    (
        330,
        "fonturl",
        ("url('https://fonts.gstatic.com/s/roboto/v29/KFOmCnqEu92Fr1Mu72xKOzY.woff2')<space>format('woff2')"),
    ),
    (331, "fonturl", "url('<string min=97 max=122>')"),
    (332, "fonturl", "local(Arial)"),
    (333, "fonturl", "local('<string min=97 max=122>')"),
    (334, "fonturl", "<string min=97 max=122>"),
    (
        335,
        "fonturl",
        (
            "url(data:application/x-font-woff;charset"
            "=utf-8;base64,d09GRgABAAAAAHwwABMAAAAA4I"
            "wAAQAAAAAAAAAAAAAAAAAAAAAAAAAAAABGRlRNAA"
            "ABqAAAABwAAAAcZSMkyEdERUYAAAHEAAAAIwAAAC"
            "YB/gDyR1BPUwAAAegAAAqmAAASlPRn+UZHU1VCAA"
            "AMkAAAAHYAAACalgyZBE9TLzIAAA0IAAAAXAAAAG"
            "DY1Kp0Y21hcAAADWQAAAGIAAAB4tENdWJjdnQgAA"
            "AO7AAAAD4AAAA+EysM8mZwZ20AAA8sAAABsQAAAm"
            "VTtC+nZ2FzcAAAEOAAAAAIAAAACAAAABBnbHlmAA"
            "AQ6AAAYDAAALEUxhIQlWhlYWQAAHEYAAAAMgAAAD"
            "YD96yQaGhlYQAAcUwAAAAgAAAAJA98B9hobXR4AA"
            "BxbAAAAjgAAAOkt3RQkWxvY2EAAHOkAAAByAAAAd"
            "Q97meMbWF4cAAAdWwAAAAgAAAAIAIGAeJuYW1lAA"
            "B1jAAAA/wAAAvIRslKh3Bvc3QAAHmIAAAB7QAAAt"
            "o9HZU9cHJlcAAAe3gAAACwAAABM/UDUBp3ZWJmAA"
            "B8KAAAAAYAAAAGcwpSLAAAAAEAAAAAzD2izwAAAA"
            "DKk15wAAAAAM5SI4h42mNgZGBg4ANiOQYQYAJCRo"
            "ZnQPyc4QWQzQIWYwAAKyYC8QB42pXXW4yUdxnH8Q"
            "fo0gqV7rYmJo1xjYUAUlsJohRsxWTZblcTD211pV"
            "ONBvfGsqGu2zjoEpPhNCReGAUWoe26bbctLFzYMr"
            "wE2mYzTPZispqadKfDAJN3zSZeeml64etnBujBiy"
            "Zm8uX/7nv4/5/n9/z+B2JRRCyLL8VDsbin91uPxY"
            "qnfvLLobg7bnE/sixazz98vejnP/vFUNzWumpzSy"
            "xut7fFoq6h9ptPxXzML/p3zC9+Zska/KWjp2Nu6V"
            "RHz9K5W/94619vO9jR84mJjp5lfcuO3PrHJX9Z/s"
            "9b/3r7eyv2Lvp3x9wdm+/4rd/f79jc+RnX7b86Bz"
            "vHuu7u/EznWKvPJWuWrOnoifmue7vuXbKm697WnY"
            "65D36tcT74Ga/9u/29jp52fzd/v33/9+Hr9u/291"
            "pjtHJY/Mz1f1t/t+9si2VZb3Rmn46urD/u1N7l75"
            "XZ27FKu1q7DhuwEZtjfWzBg9EdW7Xbsjfj4Wwi+v"
            "AIHsXjGNLXLuzGHhT0tRf7sB8HcBBFHNLvYRzBUY"
            "zhGI5j3DgvGONFvIRJvIxX8CpOGusUpnAaZ/Aazq"
            "KEc0hwHhdQ1uclbUW/c+Kq4bKcr+Ka6xTz6KZATf"
            "Y12ddkX5N9TaZNmTZl2pRpU6ZN0ddEXxN9TfQ10d"
            "dEXxN9TXRN0TVF1xRdU3RN0TVF1xRdU3RN0TVF1x"
            "RdU3Q10TXj3lgqnuVY0a7LiGhmRDMjmhnRzIhmJj"
            "ZlfbEZW7JCPJjtiK2ut2f5eCKbjie1O3075NtdGH"
            "a9Wzuq3aM95PvDOIKjGMMxHMe4vsrainbOuzW867"
            "s6GrjS1mqEViO0GokFf39exP2cVBV1v6j7OWqEm0"
            "a4qcpJ1RvR/k60zfiaCB/McvGQamx1b6f3hzHadk"
            "qVU6qcUuWUKqdUOaXKKVWR5USVE02/aFqVGxFRv4"
            "j6VbAVSX/siKV8ucxfy7Ur0JltoeHvRLNFNCPx2W"
            "wyunGPZyvb0Y3Qtkrbatwn5w3ajdiWFVW8qOJFFS"
            "9Gf5bGN737Xd9+D4+695j2ce33tT/I/hAD2TvxQ9"
            "fbs79FTh9Pan+UTcVOYw2JYReGXY+YRc/gV97d7d"
            "5vXI+6v8d1wRh7sQ/7cQAHUWzXrapuVXWrqltV3a"
            "rqVlW3qnlSNE+K5knRPCmaJ0XzpGieFDmxyIlFTi"
            "xyYpETi5xYjNeNf1ZbwjkkOI8LuOjZG3gTb6FsjE"
            "vuz4mxhnfFW8dluja0V3DV9TXPUsxjoaUxb7ytIu"
            "/wxtvxSR5foe3ksDtxl/ursCk7wR9V3kh54wxvJL"
            "xRjW94/yfY6ZthjKLg/b3Yh/04gIMoYty3FbzrvT"
            "ouG6OhvYKrrhdaK5mIekXTK5Je3qjGPZ6s9PVqrM"
            "N9sYEP3uGDVmT5eMDzzfTfkp0U3YDIRtR4Sn3VVh"
            "9D+tiFYdcjWU88g93+/o121L09rg/p6zCO4CjGcA"
            "zHcUL/z+I5PN+egwP0fkcmA/Su0rsqo14Z9cqmVz"
            "a9dK7SuUrnqqx67Xhd2aBIBkUyaPRBow76etDXg9"
            "4e9PagtwfjftXIq0CfCvSpQN6cKJsTZXOiWzXy5s"
            "FG3k94P+H9hOfLPF/m+YTnyzyf8HyZ18s8XFaRvI"
            "rkVSSvInkVyatIXkXyvJfwXsJ7Ce8lvJfwXsJ7Zd"
            "5LeC/hvYT3Et5LeC/hvTLvlXmvzHtlvktUsk8F+8"
            "zpZRTqzGZFPyv6GRHPiGBGBDMimBHBjAhmRDAjgh"
            "lfzvpyVlW7rDwrOWw11mEDNuLhrCHXhlwb5nlVng"
            "05NsznKXW+aE2dUeuL1J2m7jR1p6k7raapmqZqmq"
            "ppqqapmqZqmsq7Ie+GvBvybsi7Ie+GnBtybsi5Ie"
            "eGnBtybqh3Ks+Gik2r2LSKTavYtIpNR28s/U8tlm"
            "MFOuOrMtkYd2rvkddKfl+NddiAjdiUfYdjezl2B8"
            "eu5tjej2R5M8Pt2e9ld0Z2v4+d+h7S7y4Mux7J1n"
            "Pyetlu5OT1MereHteHvH8YR3AUYziG4xg31v+f+Z"
            "l25hXfzum/hneNVcdlOTa0V3DV9TXPUsxjwb213D"
            "DMDYu5YTE3DHPDMP/2Wr9nZZzKOJVxKuNUximnDH"
            "PKMKcMc8owpwxzyjCnDFtDZ62hs9bQWWvorDV01h"
            "o6aw2dlVUqq1RWqaxSWaWySmWVyiqVVSqrVFaprF"
            "q1TDlvMeeJq32i+t/T1CZOe8As/7gT0AnPn8VzeB"
            "6tE8xj78/fro/M4Zw5nDPSifYcXq1dh/uy1lp2wo"
            "gnaBI0WUuTtTRZaz/7urmdM7dz9FlrbudotNbczt"
            "nPtpvfOQ7p45Cn7Wc7uaTPfraPS/q4pM/cz3FHH1"
            "f0fewacMjYh3EERzGGYziOF8T0Il7CJF7GK3gVJ8"
            "VyClM4jTN4Da8b96y2hHNIcB4XcNGzN/Am3kLZGJ"
            "fcnxNjDTfXkWtIMY+eG6eG8Q+dGgrUbVK3QN1xK+"
            "M9VB2n5hgVJz9yFvymEW6eB3+gPtd3+yaFmjd2e/"
            "MoWvOoaadff2Onb1JsnGLjFBun2DjFxik2TrFxqk"
            "xSZZIqk1SZpMokVSapMvl/nytbZ8o51PDBrl340K"
            "5doEaTGk1qNNu79k8p0k2RAYp0U6SbIjsokqPIDo"
            "oM8FuB3wptZVor0Cr3bq5C1z13fSXalo1SK0etHL"
            "Vy1PoOzxV4rkC1HM8VKJfjuQL1RnmuwHN/4rkJns"
            "vz3J8o2k3RHEVzFO2m6AaKbuC/AlVzVN1A1W6q5q"
            "g6QNUBqg5QdYCqA1QdoOrAx65cLxj7RbyESbyMV/"
            "AqTur3FKZwGmfwGl43/lltCeeQ4Dwu4KJnb+BNvI"
            "Xrq1xOJXIq4RQr3jou07OhvYKrrq95lmIeC+59ke"
            "rJDZUrFK5QuELdCnUrlK1Ttk7ZOjXrlKxT8R8UrF"
            "GutZ4nVEuolFAnoUCFAhUKVChQoUCFAhUKVGRZl2"
            "VdlnVZ1mVZl2VdhnUZ1mVYl2FdhnUZ1mVUkVFdRo"
            "mMEpEnIk9EnkSXM1bJGavkjFUSTVM9/yWipvNSyT"
            "mpZHcp2V1KdpaSM1LJ+abkfFNyvik535ScaUrxKb"
            "1M6GXCubGppwl7WrH9f4it2m9ot2cLXFLW84JzYq"
            "v3Cb1P6HFCjxN6nNDjhB4n9Dhhz1xqN12OFdH6f+"
            "+v+fnX/NXLV85uMchXg/w0yE+9auTMhcveaWiv4K"
            "rrBe2dtC/Tvkz71smodSoq07BMwzINyzQs07BMw9"
            "ZJp0zDMg3LNCzTsEzD1smmHLeLIy+OvLlS58M6H9"
            "b5sM6HdT6s82GdD+viyBs/H58zM6fe/+ou16vwMA"
            "368AgexeMouL8X+7AfB3AQRZz0/BSmcBpn8BrOoo"
            "RzSHAeF3AJNyO4xejTRp92Z9qdaaeSZU67d2EVNv"
            "sfTev3oB16q7bg3l7sw34cwEEUMe6dChbFU6qznB"
            "4r9NZFYftmrIrVsSa+4PR2f3zJPP+y8/tXYlM80B"
            "7ja0Z4KL5ujG1q8kj0x7fi2/Hd+F48ap/8fmyPJy"
            "IXT8aP1XgodsXT6jyixrvVeDT2RCH2xr7YHwfiYB"
            "yKP8ThOBJHYyyOxYl4Np6L50X353ghXoyXwiocr8"
            "SrcSqm4nScidfjbJTiXDi3xoWYjkuymIsa16gUv1"
            "xxYrkWaczHwn8BLzKprQAAeNpjYGRgYOBiMGHwY2"
            "BycfMJYeDLSSzJY5BhYAGKM/z/z8AEpBjReEw5me"
            "mJDHzFpQXFDCJgEQYwCZRhYGPgA6tmZBAAizMyaA"
            "CxFBBzgGV5GF4A6QCG50DSF6zHC8jiYWBmqGEoZS"
            "gD8pkZRBnEGMQBpuEQMwAAeNpjYGZRYZzAwMrAwj"
            "qL1ZiBgVEeQjNfZKhmYuBgZuJnZWJiYmFmYl7AwL"
            "A+gCHBmwEKSioDfBgcGHh/M7EV/itkYGCPZJynwM"
            "AwGSTHwsu6G0gpMDABAGrHDcB42mNgYGBmgGAZBk"
            "YGELgD5DGC+SwMB4C0DoMCkMUDZPEy1DH8ZwxmrG"
            "A6xnRHgUtBREFKQU5BSUFNQV/BSiFeYY2i0gOG30"
            "z//4PN4QXqW8AYBFXNoCCgIKEgA1VtCVfNCFTN/P"
            "/r/yf/D/8v/O/7j+Hv6wcnHhx+cODB/gd7Hux8sP"
            "HBigctDyzuH1Z4xvoM6kKiASMbxGtgNhOQYEJXwM"
            "DAwsrGzsHJxc3Dy8cvICgkLCIqJi4hKSUtIysnr6"
            "CopKyiqqauoamlraOrp29gaGRsYmpmbmFpZW1ja2"
            "fv4Ojk7OLq5u7h6eXt4+vnHxAYFBwSGhYeERkVHR"
            "MbF5+QmMTQ3tHVM2Xm/CWLly5ftmLVmtVr121Yv3"
            "HTlm1bt+/csXfPvv0MxalpWfcqFxXmPC3PZuiczV"
            "DCwJBRAXZdbi3Dyt1NKfkgdl7d/eTmthmHj1y7fv"
            "vOjZu7GA4dZXjy8NHzFwxVt+4ytPa29HVPmDipf9"
            "p0hqlz581hOHa8CKipGogBn7CJTwAABA0FuwCPAI"
            "QAlQCcAKEApwCsALYBBACRAJ4AowCoAK4AtgC8AM"
            "UAywCGAHIAmgDBAJgAuQCzAI0ARAURAAB42l1Ru0"
            "5bQRDdDQ8DgcTYIDnaFLOZkMZ7oQUJxNWNYmQ7he"
            "UIaTdykYtxAR9AgUQN2q8ZoKGkSJsGIRdIfEI+IR"
            "Iza4iiNDs7s3POmTNLypGqd+lrz1PnJJDC3QbNNv"
            "1OSLWzAPek6+uNjLSDB1psZvTKdfv+Cwab0ZQ7ag"
            "DlPW8pDxlNO4FatKf+0fwKhvv8H/M7GLQ00/TUOg"
            "npIQTmm3FLg+8ZzbrLD/qC1eFiMDCkmKbiLj+mUv"
            "63NOdqy7C1kdG8gzMR+ck0QFNrbQSa/tQh1fNxFE"
            "uQy6axNpiYsv4kE8GFyXRVU7XM+NrBXbKz6GCDKs"
            "2BB9jDVnkMHg4PJhTStyTKLA0R9mKrxAgRkxwKOe"
            "Xcyf6kQPlIEsa8SUo744a1BsaR18CgNk+z/zybTW"
            "1vHcL4WRzBd78ZSzr4yIbaGBFiO2IpgAlEQkZV+Y"
            "Yaz70sBuRS+89AlIDl8Y9/nQi07thEPJe1dQ4xVg"
            "h6ftvc8suKu1a5zotCd2+qaqjSKc37Xs6+xwOeHg"
            "vDQWPBm8/7/kqB+jwsrjRoDgRDejd6/6K16oirvB"
            "c+sifTv7FaAAAAAAEAAf//AA942qy9CXwbV7U/Po"
            "tG+zbaF2u3JEuyJVuyLMt7vNuxnTjOYmff971NQp"
            "umdElJk9CWdF8DTUpbWmhnZHV5KYXQAoGW9f/eC/"
            "uD/wN+YH4BSqE8slj5n3tn5C1OW3j/9hNpFllz7z"
            "nnnvM9yz0iKKKdIKj1zCKCJmREnCeJRENOJnH9Mc"
            "lLmZ835GgKDgmeRpcZdDknk7qvNORIdD3F+tigj/"
            "W1U95CKfloYTOz6NKL7ZLvEvCVxC+v/oX8L+YNwk"
            "j4iLlETkcQsTyjIPSSWM5METGS8yc44jyvVI2hf6"
            "NWJSGP8Sb9GGdK8Fb9GB8gY7zVxBp4HZPNEryZYQ"
            "1cSbayKlPdRKWSbsps0lIBf5wysikWDmWBOP1Lk6"
            "/S4UwEjMZAwulM+EzlX6KlCuktUqWU3uOMB8zmQM"
            "JREkf347SL6hl/LT68dEU6vWLpMIHH/CT9MnUfjF"
            "lJmIlKAiZJxDh1Ki9TEHJJjDMkSc6CB01rxjhaz6"
            "tgiFrNGG8lY0RlFRqITEsG/KFwcPLwyZN62uV1uq"
            "RkR/FIcj8ZKfzopK+01Hdy4gg/P0MQku3w/Caile"
            "wgcnVAM648ldPDOHjWlErlGyR1ek1slPE3t5RaU3"
            "yDZGw0Vp2uL7Um8yoZviX1zWlFt1RwS20wOuEWyb"
            "UluMrzfLl5jCvX8yYYtU09xreTMa7Gcabp7F+3Eu"
            "aYkquLaznNWb6Guchw7NkzTS//1YKvG+E6/FlKDd"
            "eNet6pvsjV6UeZOo0RnoZf1eiVq9GPGmpYOCjXj/"
            "rKnXDdj19j6JVL6UerU0bhY2n8MfiS+uIfNuMr8J"
            "kW9JnROcVPtqLrNJHTpGri8TjZYmakag0L0/L5Y6"
            "nqdE1dfXPLnNb4bP9xLQ7ElOqaZjJlDGSayHrSiF"
            "7olDGVtJhNMjoFLIrBOTqT6sgADfxKV9dkjAEa3T"
            "DCaRD9KUkqftD2Jy0TLHu6V1ruWbB4fPGgw0fO/a"
            "mWCYXvaXvd7oBP/HXxkM1Lkt1XeuRx9+CSh20Ocu"
            "946xXyt4xGWkcO9LlLyF0ef2E+ybntcqbwYL/bWc"
            "hZ3YxKWl+YF3BK5Ay5tvBQyEvytp3kAKzGHVfVkk"
            "bpvUQt0UYMkEuIXA2ShsoUr2DGuPZkrkahjI221F"
            "QrYlw6mXMgUTWl+KB0jGsGjs9LcLrzfBaWU1bP9w"
            "DHY7Ck5gscb/7J5QcwZ9PAWfNZvspykbOfZbi0fl"
            "SRNhtjZ5p/evkUfEA1qkSnzKgJvXFV+lFHlR24E0"
            "Sv6EOn8YdC6JQZrURv6Dtqpn9Hk/AdzcXvaJ/+1w"
            "PoNAcP8h7zHgtItawhyzVlc3AZHYWyxKtKsz1U1T"
            "QgspVs0SuUJrPdEQxVVqVrmprbB2bhPMlndaA6pJ"
            "JsluthOX+Wixk4F6iRoII1vEJIdC5PrNQK2qSZbC"
            "KRNrEa43QaNAs6rydlbtIqk4ZBu4RpN400jY5EIp"
            "GJk0YT+rCWJpvg83B/R43MqTUmu9Z3hLqP7Wir2/"
            "3MVhXlsLZptz/RVG5yamP1A0l/7303dDbuf3GHhn"
            "Ja2nS3fOdUoHFBpadtTZOHLG3b1BUIzRlOvrxQpQ"
            "snK+WU11Kz5PC6uffu6pI8YPS8o1lU3+4h7X6/rK"
            "C3Vi+8ddXyUzf3S18xun+vWkf/rqonE2HJ70kjTX"
            "1X7NJE1+psqjcd1hMM0Xv1z8xV5h3CQJSALmsjlh"
            "D3EzkjyFCuGV74HslYzgpCk2OQcimXjOWHapoZTY"
            "wfgkO/Fh/6JWMkN4w1nks9xrn0fBgkSQGHCj1fBY"
            "ftcNiu5wfgsA40ygi8h12sISdhtNlslh9oh+N0TX"
            "MWKfAhIzCkqg4ul/vhSEFks2h5NlFTdTmmPpA6QA"
            "OZU8kmCtE44NdS5PQPZmbc7q0a3n/sE0uS6ZEDcz"
            "r3DVetlsQ0bvudl3+gdJmGS6paQ6G2pMuVbAuFWq"
            "tKqKfQpw8MV6VH9uNP3zfzE5KR4aOrqqpWHR1efP"
            "eKqqoVd18eZx5hjZfWaVjJSNuKOoejbkVb+8oGp7"
            "Nh5ZU3h4+hzx4bXnwEffbI4rbl6APL29pX1Tud9a"
            "tAry+8+h7zOPMdohks4R1ErhpxoR5xoYMZy2kQA1"
            "z0WM6lQSvaZVaAcezDVG9RjnEtel4OdPXrxji/nr"
            "fBoUE7xhn0fAwOE2B9+tFdOVhHJsvZ2FFNfUcPiD"
            "ZnMOTKarsx6V0dSOjlhmRtDxZ6RHUQ5bRoSUHUka"
            "WK04iSZiTfwAasMLWkDBmxBOLIBNVrMnC6sHLRvo"
            "6m+pqNDywbvGtNLbOXkWhsjMyb7EqkFtR72EAmTD"
            "4bjssps2EP1d1OVvPuUusDqy8/suCpW/r+rWr+pv"
            "QNr3YWBnbsII9037Z9sWfeQ/Pn3rkmW714b5OF1B"
            "qd2YTbWz8vXjqnrTNcGOp4uM9o3f/r9YVva60Prt"
            "ie2fLozrYNnaULO8lX6k4TJLLbZB222wHBaosmm+"
            "RURXuN/6lnGuonZ7HJ8H1HC/9BaaTVsHKMBMm58H"
            "cgQ+8W/h6Rz+omwYZIgV4GfAgGJE4f9bRu7x9UaZ"
            "Xbd+3ZKpVoVYP921s91OFb/vT73x1IyoxanelMYe"
            "3PflZY94aKlWuNsqr9v/vDBYQ9SKKv8B/kD6Y+U3"
            "qeN0080wp2qcaQrqbCyEQ1k4JWkoXjZJ9EunXPru"
            "1K8WHiCP5dzqreIJ/62c/Ik2eMenhS8sDvfv+nWw"
            "5d+MPv9lfJ4AnEWvpG6rtSKeiGFAFYLC9XEDJEsa"
            "oE5z/PyZJ5nxmBNU6R5JMgYT5YtKMStTsEEoRokL"
            "FarHGykUwBFZrIZtCXWqQ2PSQaU9hiNQeQKQWqhD"
            "M1IGvhtRW5UotCvkynKjFwFRwbMBj8Br6CN7hVen"
            "KZVGby8xVcMGQqNTKN/RtNPkPI9O47UY/OqtrQv1"
            "GloSiVanPfRpVV7y9759vGUr3DtKFvk4Wi1GqYy0"
            "YiJ6mXvECoiAUERyQ4WYon6TGOSeYIEq0pQqmI5U"
            "gCHZI0Wl7qBKc8z1FJrMwkyZxCie4pZPAxJbarSk"
            "IR4zUCu9M+EBif2ccG2I3kZx8mP1dY/TC19Tj5Sm"
            "Hu8UI3+brAv9WFP5DDxPuEm6gmcg4R7bKIop4EZz"
            "jPEUmMdb1AS6UBFCBjR0uTccDCVc6AtbJwKFydaa"
            "IzaNGtNngqnJ5Kr95Z1R33aEid0utzSjXOmMdwqy"
            "3iNVrLqj3xdWtWhN1qVqNTharq/QZ/XBjTRspOra"
            "ZGAU34EV14UjqG/pGcJMETZCxPqwiFJMYzxXmaN5"
            "KXKPvjj6O/XQr4fQTmYyQS09H7lGMSwPoUEM+b8R"
            "fNROhLTT4Exydh+UwsjsZ69W9Xn6HlzG8IBaEjYH"
            "wkp0wggA3fJ5uwCmpqkybp6x9/Spt1LWfGff5L0l"
            "I/6NfGq3+RbAT9aiTC4M/gEfJ26Zhg2wJowmV4nC"
            "YDuBd6tKSAB2N8BN7dky5GACAHrxTtkgHjAtJgNl"
            "FY+ZHX2JzGBZ/52p7db983NHTf27v3fO0zC96sXX"
            "movf2W1bW1qw+1tR9aWUv94muk48zSpWcK/+drXy"
            "v87vWlS18nHWef//Xxxsbjv37++d98uqHh079BtP"
            "4+4P5a5itgq1uJnBSNXgbiSyexLiM5Y4KTgxJTju"
            "VoORJOmgE5ldPoUC4FOUWInkYWgMRLk8TSClJbnW"
            "EENfd9Mv8Q6b3yJ7KeLlxSmhU6u1TSQd506bvHj9"
            "NjFfFfyeVaBRrHVhhHI9AxBesoR6BxxBVjOS+iok"
            "4KPhrhlcNwqtHi4YOGsZwyOLFW0sAqPk4ABSUxAF"
            "1WFvxHeNcZckpnMItomkIKQYRNiH6CCk7AlQm5R2"
            "DMl7S4yK16ljxft6ajNNK1JpNZ2ZNUyn1ORiVfpV"
            "1w6+mVK07tb69auLOxcGtkKExecPubjFGW/HH9zl"
            "17W+asafb66uZXugGU69c/saWmdsujqxccv+0TTY"
            "UbVdqAcxea566r70m6YZ69xDYi14nmqQVpqRalJW"
            "+3dVYD/LEj76qzGk15boJzA5wGDrizaMpuG8But5"
            "4zes+z/BzlGN+Hpm8D9Joj6DkI+8Q6gR9NWS7A5t"
            "yJLCaAETRh0gLWwuqmJwQp/aEEQZZFWES7+sFL8c"
            "kpCaWNdG7q77+xL+wZemDvisMLQ2WdqzKZVT1Var"
            "nPwahkIo1O7mkpbV1Vd9+J/rv4jfs+f0MvucurIA"
            "0GiZJx97ZWJOevq6xf2x05TIbbV2abVzV6fHWDiQ"
            "mqpdcdX9S5Y6iODeU+teHU3oa6jffAOouAfBwEO6"
            "snLEC7nArhGGxuNayK1MQ4OsVrQHDl4IBYE5zqPL"
            "jMvBLQCihaBFyUKqCJBVYaCz4cR2Q5koVTQWLBjk"
            "hjJKhYH8hrBmS3JhOh3nnhv0ymV58e32/ySPKUhm"
            "YoOT16ZfFmvbVwK3mHjd1IrXQ2egVdh3hqhbHFiV"
            "uIXBTxVC4VIC7vko/lvYaoFXjqZUAZJBKcBgQY2F"
            "YpekSaD2TYI7KCR2Q5y/Au60Ut5z5L8BY3uByjFq"
            "vLPeFZBGHwvA1rb3lUmIeBzdFmQcqbyRrEvUlFIZ"
            "WFjT6YXMBH10zVirsyBo8x3bO+vf/gkkTZ4CcGMw"
            "dabjhAvVZVV0i13bDmhpcONC647+zuOfs3DR8bcH"
            "rD3qrlnxzo2NUfM5h81EvzwqnCJ+3t+0+t2vfmp7"
            "rssVocP0A06AMaOIAKI0TOgqgAM87JEI/UUYsMKK"
            "CmBQqAOnSCe+jU80FgDSsQgw86QYTVFgXMhWNZTg"
            "6TjHphvgQLF9Qsp8DKEa1jpJIF3+l6oHzX4hXkjd"
            "qU71SBrv5k7/BTBzp7D5/ZvWv0k+1cZN6ent79i+"
            "IVgzd09O4fKid/s/qnh8i/GRzjt5SW1e0+vXnDy7"
            "f3dN5xZqhr38J4YuEN7d03DMaSi3YDn/eADC6GOR"
            "pglpMSyOvlY0hRIl0o2jKTVIadNgRmQaj20L/VKc"
            "af1iTK7qA2a0oM4xRbwjTfsiBQrr+ccYYkZxwxYw"
            "lYyrVAw2bQDR7Qgp3EbUTOiagYBHyuQs9ppcfy2Y"
            "RTBZTMIlnqwpT0gmHx6rlqpA6kANqlCb5aiS7xUS"
            "CqBe42oVt6oHI3XGiqBkCuMjqDCRrBKT4RBBJbwP"
            "JwWZY30vDeauD10qxI7EmojiY1qRNmEtwonounax"
            "88euO+5n2n1697Zl/z/huOPtB9x6s7d756Z/eb0X"
            "l7urv3zIsmF+1qaNi1KBkpadnQ3b2h2eVvXT+ndV"
            "2bjzxxz+vh2IsHBu/Z3NCw+Z7BAy/Gwq/du/rxHX"
            "V1Ox5f17ihp6ysZ0Nj3/Y2r7dtO7Upuaq7vLx75c"
            "al7aFQ+1IhlvYo8Gm+SEeQRRNR9DUToGGdQRNyMJ"
            "1IFqsFCk6SS6kfQ+aE94I14UxZLsrmGKcNezJIPD"
            "lblkuwE4Z6CmyaYPdUd2WSPI923Z7btuWLB9tsFU"
            "1lhcc1Vd7nya9aQ4bae+cNPbin9dWyudvaevcMRM"
            "oHtre075oXo6+ufv5Qd+ehl7Y0792xPTt+xWijet"
            "xpR0Uis/2pvZ07+sJV87bUduwaiMJf4DnfCZjpDv"
            "p9jEPqp6EmLpDI20XMhLEIZ8J4kHMnMQxRwlxzOn"
            "sAzfJaBMXOOL9zJqL6KIRFl1wLudB4AbMK400Si4"
            "hcHI3XjlErp0tMYLxUgjNh/OqG8SqFN7eeD8FbKM"
            "FRKb4a4agQ+Ac6O1OBBdoeBz65Z/AHe/jXg7nXTE"
            "k6Bfcm3NNw7zXB3PHIhyFhEmTxPZqT7AWdQRjTyF"
            "mTMdhbsTJu0kU+Stv05kK1Ke5wxE3kd0wsdbyPfO"
            "IO1qEaU+mljF71e6VTfyei1+PkWxIFfRrHyh0Cog"
            "ZzB2CaQYIsT/CKCe1Dwr/H6V1XTtC7yLeOHSOXHD"
            "sm2Km/Ee9JVMJYMggRBZtQWCkcRM4Uaf6b3ky+a6"
            "qwO2LmQtrE0tb37tQ7lb9XsoxUrxpTOgx3FDb0Yd"
            "4Frv6FVsL6KiMaiJuJXC3iXVxA6HiZ+ZB2akxwkf"
            "N8SjU2akhF5DHeaB5DStJgHssZDQjIGE0KFHflS5"
            "AM6sb4JhRfMAIrGV+8FoURUixnz3Lgs2mBrT5ga0"
            "5pCCHrwLBg+WCuE2hFoiMDYVBUkuJ6C/jDM9kf8P"
            "Yt29o4eHx9Nt65sKdaYjykjnfN74oH6gfjTUsbQ2"
            "qH9jGjPzEZxI/7jUytOx2xJRYf7B/Yvag5U+t76g"
            "VVrK2uvnNpU3lXyuEOemyX75op3hQxfFUlbWV2E4"
            "PEGuJNgutP8K2SsVxrP5p1axfMGlC1PZWPS4gUiP"
            "uiJLciwS1L5QPCeW2Cl6Ho11qsnBqBbI2C37AAHM"
            "YFej6FQv/mMX6dACAa//rnP2IAsQwAxNKzfEhykQ"
            "ufJXKh8FIUsxyF92UTACK1gDW8JjPaA7WtXf2Ixl"
            "qW6wXqGlth0fRm+RVA5Ve0hNuSaiyGayRARIlAxF"
            "BY0G6CSbBYaal5hp4zmwwWCYrTAN7wkFJJwF8aoo"
            "ImC/pMxhhCHxmu3XQf9+6ulfmnj6zJNO94aPH89d"
            "r64/MblzW42+58+7aOzfHVRoM3aksu7QgvO/njQ0"
            "ffy23a+SZx9YEn/7REp7EoHDsLYzxf+N6vbqPsbW"
            "3eloYUO1CW6L9teTXVuft7ow9tqY/M3//YGzt3nT"
            "k20D9/qCsyd1P9Ev6+pRZT4d7+ZLCyRFm3+f6Re8"
            "7f27Xl9SsPvlT4R25pe7XC1t67cM9/kKmR3hFzar"
            "iNjCvmbD6CZF1JEMwWsPkawkyUC54JYE0Beiq0BE"
            "BPXoFWoCWBczE8CUCcMyCfg0xhB4j20UA10kcj6s"
            "mU1Nx3qN7HnD750fGdx2QBOymhSn9nNavMKuaNSx"
            "1OE7m2cNLgonzUosxQtAuceVjx8FTJl2EMJrBoUW"
            "KH6B+ZAWNi/BEFq+b14KF40VBiWHDMRrBqSc6s55"
            "0gMDo40+k5GYICISPSnrzMOMaXw62QGUasBiHwoA"
            "NdlvOycMpFDZwMTYP1zfAIgj7M8FAA/LziUY78jy"
            "Nfv7XB37654+nT7YfPHiycJRsX3Twv+PTpwtdJ6c"
            "KDg+Ennyn8lXkjvfbepTXrF7ebvc8fXfH4roaT4c"
            "61dXsP3xOcszJz+02go5ZffY8pA90SJ7pELO0CP9"
            "AVxfHJUoWoYYxwzchgLaJGQRUBVNpMGEmCOKNIsJ"
            "wIZ0WHuqYUiSt2cuKSYqgRy+RUOV2+/S1Sf/rU/1"
            "02R2YwaNyRTN/61v1v3zNv3t1ntrduHukPs3q9dm"
            "Bp4eorny0UXt1A/eJZ0vzNnRsWL1NqjC5fiWnw4R"
            "/ddfRHD/XrfMmATrNo874d3yRNSIaAX8wLwD81YS"
            "dqRe5pZCL37DJgmQOPXwOM0eh5IzBFBlNxoqnYkb"
            "9SZITEgoICMFyRB7DofDkqeIpUfnXz5q8W/n6qcJ"
            "pcsf/skd7eI2f3F04zb2x5q/D+5z9feP9rW54euP"
            "8/jxz5zwcHgMZInlDMUoVojEejKI5GIhvLM3IsSw"
            "wKXqjxwBRGIVSFw1gKFWgwKinErMRAlRCkEv7l6L"
            "vHW6i68W9SZ5k3ni7YniioTgn2p/hcBdEiPHfymX"
            "IGP1OOiKGc5Zm0QgySiYGZyQcKj1s6/iw8bPzqqf"
            "GjwrOQHK0GOapG66USzdEtE6MyJsVYPhipRCgwiC"
            "xVGj/Pbhrj7Hqc+42Bsx2IoecGyuCJNXAJBWhyLF"
            "OJzE+M5ZSwXNyVwJlYljdFwGYpCbVd1JhY2nBoO+"
            "yfEtnG4saCRmgkfayWFiRu69uk5YtLHrptR6KWLd"
            "GbHM3Lbll0y7fv6Zt33zcO1K8bHgj/2mgj38p84u"
            "4nl32u8Pez26lfPE+av7HDUdkRW1pCqrSRkHPwkf"
            "N3Hf/po/PVFq+JbLfq94x/p6y+zCjIHqY5M4x53S"
            "xqL5mgvTgmlaeVmOo0PcFplRG5zJwKR/6B/lhLFH"
            "mMku4p5CezuW/Rlm9968ofmDfG91L3XuqgnhxfL9"
            "D9R/ByEp5HE74pPEZhPxwNh29D/5iJb/zROaT3hL"
            "9tvPoe9VP4WyuRFj1IhRwlfDgpUlsE6Dsd/i5bgt"
            "Oe51n4HjtaIgoE1iUokIlzB7DUsUkSkvMBfyMloy"
            "mzxdCUat/SE/xmw/4v7l6ndte5WIuhbPGR5fTXr7"
            "QcfPtTXfD8c0CrpfB8P5EVaWUSaaUCWvkEWiGJCe"
            "DJ+DGt+FI0BtoHY9BnhTnhRIUYOERQDxvNMBmnYy"
            "R77htzw3Ez+XtrxPeF8R9aghZXzEp1f9HoNLLyAq"
            "My2f12GML4IZOVijjM40dtPqUiWDLeI1WyCqoL0C"
            "UzvlrkK/03GCtTtEo8LR8TaS0t0jpHE8WgHS+bZK"
            "I5d446CGS/8NzEupR+Ac9bXJecQZy3PjUxWT1OAi"
            "HdZAAnCSbNG/0wZwWsBBLF3hTurDB90LQkTBp4gW"
            "kA8oKcdmmM9IfhupnNPeAs0ZCXtRal0qol/6JxOe"
            "9/iaIKCkeFK+4YL9A084bSeuUT9nSJq9pBH7EpLn"
            "VIaixVjitb43H6MWfKcvmdKXJtLmpUTi2OWZkq1k"
            "qYYcxg/wAZ82r9mGif0Vil7JSxgouLpBpMtTC8TU"
            "67nl5AUy+tt3l1V16mKOYNjenyS84qm0R3qUNvkg"
            "w6qkyX/wB6feXVv0iZmbFfRTH2q5iM/ZomY7+mj4"
            "j9IuUxoeaRjpjpSq7c8hVSeeoUqcIq/4NTpwp//8"
            "qWd3vvPrv/wNm7e3ruPntg/9m7e6lffJ40ndu161"
            "zhAuj/P57bseMcafz8kfMPzpv34PkjR3700Pz5D/"
            "0I2SaQeckTQEct2KbWqVrZDqhCo8Myr5FOmCgtwh"
            "FJTqtHAXesHAQrpWNnwoUYaSeFcYdi5DmSP/69I2"
            "3dx757+MKFRXcMV3zp5QvMGy0Hnt+08cWD7eM/o9"
            "6uGNzd/umTgm+4svBDaS/QFWWP5xE5G6Krv0jXck"
            "TXKiEzbJrIDCO6JsUc8ChjtumwL+i3ISqbQWeXs7"
            "OQGPntmO8IGHw4qaPz93aTNz0TzNoKhbpbOz6a6J"
            "ve+NydVYWtJgdpN5hnJT6mPQPSTtiIALFMlGKdIM"
            "U4GGy1Y/JbkXouFRAOkN+e5Gx63iOSH0XQPGiaRj"
            "VMU8YC9gR2WDGk4wLsTAxnQcoIVBE5gz0nj797uK"
            "VqxZGFwRLyi+BWFO5kI5FHfrDk8EjFl178CfNG7d"
            "ZHVgwc2T7XZCkb/7copbabx5+kPoj2b51z511Yh9"
            "RffY/+K/CskThPCAlmhCkU9dh6SwC3laBZlYGOKi"
            "tB18oCCLc1JTjreb5COcZV6HktTKXGMMY3C47Nhc"
            "+9TSLHRssZ9Rx7lq80XOSSZ+FkFLxEY4yr1I9WVS"
            "aNsRy8TlZw5OAmvBGvsAZjZVVSrNyYdoY9oQprsU"
            "hDy44qSoL1yBuqMfCeAFqTZQoUTrfWIHNfwnKeKe"
            "UaooBIi06QgCpF78ctFT2l+jq5VWfJDO1b1LWrL9"
            "Kw9tbDt65raLjhCzsPvtNXqTDr2UTn+s62jR2Bxn"
            "XoVmPrra/tu/f/vrxSpY9nEuGutY1tC+vKopnhw2"
            "sG7t/dPtC7UqcPRAOljUOVjQuy0YraxYeWr/7Coa"
            "5NmPYuWMOPgxzJiDohhyOIECFHiRwUHEAJXQbMAS"
            "NFpGcATOWkGD1LUf5kMnKA8owuyaFC8JuM6bnnLl"
            "1gTPj7n7n6Z6Yavt9OZIicGce3FYKt4bSpomYAc4"
            "MK0wAr8HpBLfAaGsTQnBXz3sXFVbTLceqZb6TXnV"
            "i27L516W8MPvyTI0d++sgg1UzfcuVTyx/f1dS467"
            "HlcHz4kz85uXTpyZ+icchhvWzC8VfAFgY0T4LCS2"
            "Yi/MoTBnikHK3zmXaIDMg/6/TKyc9qbSqFRUs+JQ"
            "s4Pjv+wijzhsN8+YPAgvLyBQGJinVjMEKCPiTkNn"
            "hWGXGEyJWhOXvCqRR+IK9zpeCRkQQKGSJJfev3Fw"
            "QXnAAXnNFzhJ6X2C4ycMiX2S6eabRc+D2+HYbbob"
            "O8SnORU58l8hJGpQ4J8vkagU/CZVMLiWA2ZciPsQ"
            "QEP2bmjNCpZcr8SOxhkgHt550BKZmQa2QyrYIckZ"
            "Y6sk6/jFxi0EmVcrKSKXU8UajJFT6tUjIKpnAshy"
            "hwRVLSHAjMcdBXWDc6S7f7G+HE4ALju8obNYX0l5"
            "8u0oUxAV2cREjUWPpUjkJs0ABNShK8C7GBQlZWZs"
            "ZWVkHigYuQSCFoXBwgD2jJi4WSb1hMCvKTsIy2wr"
            "p4s+Ag//51q11e2C9TFW5X2MzU+9S3tZrxvMVO2b"
            "XseMs4Y9NSmyzG8be0NlEmKOwbV4o1RhMyYU5wxv"
            "O8AYTRIoaWcFKBMF5XRED85S85PSpyvqlEpXKayQ"
            "GF18GN//YbQCLT+DdLmz3e5gBVp3dfuWf8daob0W"
            "MY1h6H4wNuEYepQO9JkZgwyJnRJnidgLzS1RnSh5"
            "IDMtJnHjbRzVf+Q1JlvvI1unPA45Tc/3RPwHN57y"
            "n0nScKP6aUUj98ZxpH+uSSMU6W4EkJ/j5YbHmNil"
            "BJkBfGyzVjxTM6KT7KyPqElFYKJTRPkDc5rV/6kt"
            "VZ+LGst9tqv/icw9qN894Xrj5DkzjvTdCYFog1pn"
            "5/FRC4353VML/x+y7RgQD2pwo/Jv8Tj6mJ4DQJnp"
            "bgVINSHJPmPKdI5tXCQNQg/xoUnISj4uA0RTpYq2"
            "sQKBT8IN/yEgsaGnlT4civTC7pSImp+x+vouedok"
            "P0B0BXdqKuhsBVIlijGRIIiApgjlMJaTwh2BInw1"
            "IZCricInfuJ3cdZu2Kb2lMMq3tnNxqpkPUQ+PbTD"
            "pys63cXFFeeFRrxjJNFH5Oh68uAZ/FSnB0gickY+"
            "gfrlUQPBUz6EY6fOUnXziIPh+WPEe+CbZOAYgZi1"
            "teqiC0KF6NXdc8g6cseqpIyjx4cOEv+X3aRzdbHF"
            "rmOxbrPzR6jHXAbt4n8RIR8Dk+ReRK0IoKpHJBJE"
            "kJyVjOTCJTyoApNWNTqlXE8kw6aNbAG44Xklwd1r"
            "9RALtRPQqIc0ySdypxfs2jHuM8Cb4e5D+KlqTWAA"
            "atmoUvQylDzmPgvKgSJMiiA07B4sgVLjJCYR0cSE"
            "VZYSHXxgr1WPTUBEcmraVXtj/8N27TxralbZU2Q4"
            "nct/jcTUuOLE8MOr2MOdK/eG19w9aB+MvOisbSyv"
            "ldLZ5buV1Jkm7cO5yVDN500B/xG3V1QwvqNp5YMr"
            "5Z71wdrCsz+do3zYvUh1hjacr3Y4k31SnWSF99X5"
            "IAebUQQWINkdMjWhEp3kHDgkNk8qMDP7ZrJEIXIU"
            "wYqxK7aSbtGGfVI/XEqwBkhMUab86Y5VwsL9Ujs+"
            "93gL5VoSyY6MlNzTKGwjJpIOOGKzXF9OqTr815dv"
            "vSE5syjTc+t3n9PdUKeVlr1Sd6TzxZ2r6uaeRTtc"
            "xvxh+cu6z9U9+8/YZvfGaov3Nl+FJ75vtf3XD/it"
            "iCXrH26ur79IMSNxFFleohxHUnzMIZQrNwelG8C8"
            "2MhWssttgsjncJIT6LAUfwCJ51ojxpQAiNyydyHz"
            "PBCeYYCngJV/tabnl1/47nWyrler2uNDOvru/GeW"
            "Wxgd0dbYuzQYNNlWp/Z/ea5w92UrIbv34fcK5VpX"
            "F47DUb71+68v711d6wh20b6us6+k3Mm16Yx89F3m"
            "wTrDN4zXwJSC6eQQBmwAQw/FBM4Y0BMwXALCdP8i"
            "rglEqPyt2Q2kQc8iCUxhgwSuN0yFkqQTPVXTtT7M"
            "LNrBZk43Aq7W288dktq+5Ov7o9rNK3vbBr5P6NmS"
            "8H2tc2jxypTX6i58STFH3DN+4fmlNHNV8qCd06uK"
            "b9rnO3bzgBPJpL/r09/T08P8Sn/wI+lRIVxCYi50"
            "WcMtLi5GKSsXxQ62VgUQYlRClalHHBXYIZBbG7FE"
            "JOinaMT6CIKyrWYIxetASVLG9zItnToiJVdMgFWZ"
            "5QTs8wSgM+oXADc09YgT5BCPtaD+X3rDh5Q4vBMT"
            "5AVS0+0NuxrqvSYFWn/Cu3781uzx/uPUNV+9vXNh"
            "1+hCrZ8eVj85r3f2lnzLX2/jWV3iDwMNIQNnbf8/"
            "3/bt7aH30My6QTJvwM80OwaMNCPVlOiSsthPyERs"
            "/L6bG82eaQa2KcM8WbwRSQSVxs5sCVbXaYtSmZsz"
            "sQu+1WEGKHHR06ENr0imgzJQBoK66EB/l0kQL+DM"
            "dJ5+nUijsXdNZLSJ/bk+ivdZHlhZ++vtfipF+au3"
            "7k+Koq8zKT3J5Z2rbq8JXj9F4NYyFwnDxVOEb/Xe"
            "IBTdpPLCdbAA8g5iyD4S3Tcw7veTZvxdzh0gm+Ga"
            "72JLjuFB9jQHEmOX8C1R+TKBejPc8P6MY4AkWd6k"
            "AYB/RcGKfXNdjmhfElfhg46dKOjba7huUxvlI3lq"
            "tsR7OstMEsVwrI8H92nn1Q8GHm6rmus3yEuchVnG"
            "VGo5EKtD0BvZ75x9/PrsP16OXolIFPjnbP7QK/Bs"
            "4n/RoiPzdSXtElwsW5kWgMzrqn1Z0PwOd4WpXN8s"
            "Nh1vCK35huHlqG3BrQbxYsYz3NIHiVRJ0YxaSznB"
            "+coDCcDbG8qhTeHYZRl3ZgGIcyrRP5/4kyAIsVkI"
            "WOtAgaEeDmpMszLektgVvGidOQX5pas6i8GtxQb/"
            "+S+s0nFrfvqyQdsf2B+k33LuqZ4/PVrzt0x6G19a"
            "2HXtmz98Xd2c+V9u7q671xMBbrWbN1V7Kup8mdXV"
            "STWZR13fjzG9f1b/OaOrLWykRMH7tvVd8tSxJuV5"
            "tfwbYN9t0ynGBNCWswyEoU1tTS7rZb1tRX9K0bCD"
            "SUO5xV7ZFwwq5hpPKSedSv4v0ZjyfTH1+1Zw+S+Y"
            "dBgD4A/WUG2ZmIJ0mLulfKTloVMbaErQoKLyGPR6"
            "3E4SVeg1IoUlRmhJAJPd2KoEhYCJkN9uFX257euu"
            "iOJRWvbds1+Ol6MBMnuxZlN50YGd9OPbj/joHWcQ"
            "nSOeBZk0eZ84QRcEmzWMeA8MDUDLlt2lYmE97KBB"
            "YPRUan7GGaJRsO47oLpS2diVKzuRTlOwPm4KvMkL"
            "Oy1GQqrYRz9B6/9C2J5vJfsf29eq5wHI/HBF5RI5"
            "FToaEAFJPQY1xpIu8QRxTBNXoa5dioXmnWgg6Hsa"
            "HaCo0SRuMoxaOhi3Hamgl3MDRzhFk5pdXLDdr5ba"
            "/6OncP+DOnZw638Lm1GjnZOyRZevnZpu3z4yrpmz"
            "NGL9jYJwGj64G3bmLOZFwXjT5PuwhUTgNgNke7cK"
            "gUs9iDieoWgIMXx3ddHx7fDQkxFfbJ1xImJ0t26T"
            "yOZwt3aEsMBo+GfOi01astvKjzeFLMb64cVbHkKr"
            "25sJV1KtU+U4FgjeQXLJqCHo/1BXhZDWOliRJhrB"
            "Ohc2Aq+jcZOn/hVeY3l0oE2ZUaMYbYJvogFh/4qi"
            "TMMCcnsYvGh4BJzmQROUThixBEB8DoRxXWMFOfHk"
            "uwRSlkBaMSXNbP+wSIzVlZmD7BhxCKZFTF8KkHY0"
            "NwQumpEq6dJuvbw0pt9t6VW+50WTqH16UW3DaSeH"
            "XrxorBxtJXN6xuv6FSoglt7Vq6b13tgrQ9vf7+VW"
            "gN3Hyrp2lFEzo6dLC7+cplgY94jcI8rcSAyEftlF"
            "kic1xcD1ZxfoAtdChdXlwQwqQ4nVBeZiTw6dSpuG"
            "cOXdf+7M7JlQpj3T13+cyVKuIfSRjGZgR/ZSIWXM"
            "QHnskInlgHXCLWAaPwXclkLNgzNRY8fTmImEbE5b"
            "0dt7+2d+9rt3V03Ibeb+/4cqh/78Djjzzy+MDe/h"
            "Alu/mbx/v6jn/z5oNf/3Rv76e/fvPyE+vT33v937"
            "6fXv+AMN6HC09LysDvQHhtlYDXMJCeJKcf7CKRKC"
            "I1kAvOmsREtQgoWiOiaItIVLAycgOahMOAwz6cn+"
            "U1DJ7KVPKKWNpOTkPSiNSAzXaPAJhu2vvcltV3Vw"
            "M0+8xTIpAuPM1sD908uLbjU+cwlG6uL6SpkelYGn"
            "hQeJr+uTinDeKcEABFS30ChQK0nIE+AYBwniTSWA"
            "jbKGCGCgF9aifQp0IQmEn0qZqOPjPYp2Wviz7X35"
            "NRK8u2v9r63Lap6LPqpgn02T1nfujSg+TFvuVT0W"
            "dr5nui3KvxvCYi9GhGCjQjAwqHW4ss4hnNGK53tT"
            "CiITLgfAcaMonS+mLWo7hu8fYbQdY3uy0STfrJrb"
            "FOl1Vm1zakV90Wk2gsJad23Gxi73aYdm0d3w5jaQ"
            "Z/1Q9y3kCcE3aJ8gpQniVoKGGJUPZjPc+X6/F+Tx"
            "TRTQulPQgN/VHx1q+mRnQTsotc1ZSIbkI/WpmoAu"
            "QDr7NHdBOVVVMiuhNnGP6UT4/ohusQ9EkbeA8yOb"
            "yiTqiXDbOjEqsvje6VGD48sIt34c2I6zbH5FajIV"
            "I31Jyen3bE+9ZsXNMXr1p5bHjj6fqI2q4rqx1Ix3"
            "urS+J9qzeu7ounNpxYtYPb2aHRuQIlzkRTIFZb5v"
            "ZGW1a01e8cqppT22YlLSU2W6TGW5YOu/2RxpHm7p"
            "tGBH+XJCquvke9y6wkfMRNAg4HLTGW02FYrUPVU6"
            "oE507h8BCVzJMY2+ZIFd5rIlUI+5xd5xE44WTJnB"
            "obODXaj+JS43IKgOMoVkObx1DUA2W+sZmjgFw6tD"
            "kE57PTGbx3FefahUKeogcZSqOIQMULUcrKLkd7mF"
            "qSrojHIteq04m7ug4ePqS30k91W0gYa+GJw+MnWp"
            "t1Rh27pDx59HbqINoHdAhk6ecSDejzhUROg+QaBf"
            "B4qaiGcCbPVjR+KLisEKAWGrQOlqkugbW6ghbceJ"
            "1gokipaKpx0NmCV+JU5/DQqzt2zL+nAXmD2vbndi"
            "y6Y7ic/AQKDO2/va+NGr/8V1Dxy2o33i/wAJSIhI"
            "ExTok5k/9EzNnwsKVESsq0rExu1JIqqdN8b2Hk87"
            "Cu9OMbgr2lpb1B6gm9BQMsHFsFO64j4uR8oSKS86"
            "bw43idB0WcE4li1fjb7gudMyPOMiHiHJddPNO44I"
            "8vCQtNoefUZ8E/vchFz555W3JB9EZCei5wFgzmRc"
            "569kxj3R9XossM54Vvc+pRMazDDt+GAkj2i2caxi"
            "6MIAcFvmxUqVCD46JCr2fe+vkfTfg6LGCT0QoLOK"
            "QfLQ0F0D5d9JqDD0/ZWqvK5uBTU9Y1fBRdD2aJFh"
            "aFv40mq8MZKI3GFMpg6JpN1WSLmZj4lMeLPhef9Y"
            "NTwudx0Ajy8ETw3CiwxigGz6eeoqwYCh4CoKOlMV"
            "L7GWsJc06uZeRm1c8Yh2nAVMKcV7EKRq94l3EZjx"
            "b4E07lb+VKKaOUj6k8R0WGdgQCHUHqczqTSTe+ur"
            "TT5+/xCuwNUF+1VdpsVbbx1gBRzNnQYyBX0+Po5E"
            "fG0Y01woAn4ugo2OkitxVOv2w1yQsKwy+VZt3zhd"
            "PktpdRFP2yTPcXpc1MGclLWk3hoKuE7NayBXb8v2"
            "1a8mG3tfBZrU0YjxbwgBzGYyE8RFHEOXMC2RZkUI"
            "CY5utKOd6yrj1pc0hJi0IjRVkHrdRlODH+FUWJ68"
            "ibEo1VN36Dtdxmq7BSn9ZbLn3NZCefgGd0Aw22wD"
            "N1E7FzDSx9GSnoAJLTJ3i2GDuvEYPnUhQ979aoqf"
            "T4D2lKqxr/PlXTpynVU3+5b8BYqh033SvqzsKPKb"
            "XUT7SQBwmuOsHHJQjNiEoSNXrQIms1J8Flz/Pyum"
            "SSjyHLGU0muZieCyFvvwk0TFOCZxpTgJ7hpjOcTO"
            "ZCTUhzhlKgOXVJvlXcwyH723+jBSTh6uJcOM7V6f"
            "mo9SIX1vON1otnvqb421G8VuVxzhnn5HreBjdhbT"
            "Fw8+yFDyrwGpLrR6VyBpaODL1yNv2o3YZaGDjQ65"
            "nmjr8P449F9aNl0TBcj6BXoZFBI5w2oNccHE9ZcA"
            "3ZHHwKHZVlc/BtU245sjl4DjoCjNCiZ6Qyuc3ucI"
            "bLItG6+obGa9bea1KZ3VEWqW+YFnBoyoJI+LLiQs"
            "OluQAyfFnOZOAVJXjZpQWIh2xqPYmTEn5cNCoUFO"
            "KdpGaTBS1H10RpKUDcio4uvZGNLrp1YU2JxjRvmd"
            "aotUZr/f7aqBUOl80zqV01C29dFGWN+i6qa8eC1t"
            "T6DRvTXQcWJwZNxk0j2a0b16fCbdUhhSKUbg2l1m"
            "/cWjey0WhaULn4QFd6A9ycMwQycqHwNHkTyAgtVF"
            "RPeFrqMfyPmdiye+GewtOy2//xSbQ3EeQqi+XqZo"
            "JrEfIgyUS+SpArT4IvFeVKBnLlALmqA9GJZkGu1E"
            "ioVFaQpjRcqqgBaVKnsUluAWnyT0qT5YOsIE3WOB"
            "eNI4RaIb2IXDWHFATm1x+swdKkinPZOAqX1sDNrJ"
            "6Xo5tv/V2QJpV+VKGSg1wo0euZ5sa/B/F1q37UZn"
            "XAdTt65Sr0o7GKKJyWo9dJYazRj2ZqsnC9Fr3m4E"
            "+myI49m4OPo6NYNgcfm3KrNpuDB6IjBRIruUKpso"
            "JcoVhVTaY2e61YKZQ2e6w8UztNrNIyECsWxCoJhp"
            "1X14FYVbFwgfMYeEskmxVzSJOSlREaKOBSBpRXKv"
            "oZIFkp86S8xcilLrV53jKdQVcUJjhcPmDGwrQwpj"
            "fpu9tB8Ez6GJzWnDcZN45kt4GwhFrTSJKq28JIkr"
            "LDmw3mwQSSpI0b1qdaF+wYmgPXNyDxq8S65wD5vC"
            "RGG0C3/4JAtd6mFK8EZ0qRzBnxZl8jAK9Rm9Ioj3"
            "HWFM/ALWkyx9hwcFyDcFuJsEdKjXM4KL6kBXF0iW"
            "URP3vrT+KGMU6BpUPKXERdEUzMxTNjP3n7A8xBqX"
            "5UJlUAB+XoFfHdbDXBqQW95uDWFK7JszkLChVlUa"
            "4e9I/ZUlzzcoXJbLFOY44GpRoY47SYEoJYqWKZPT"
            "gTB0Kdm1vTW9YMB9vmkg+EuuBk85qRUjghn+/Yuy"
            "DuD/uXdMFBha/Mv7hH8N1+CWvxl+BXsGB/5hLYW8"
            "trhP0rHliZXqEmVDWG/o26FSjM5dHj5JZbP8b7UE"
            "mVB7d/0ODdOhpPEb8K3Rum7AHJpEDvsIFfmgLxmn"
            "jABG/OkoqAqYKWoX49Cin9JfokuoDuJ9BbYrw7vm"
            "RkZTq9cmRJfPy8MN57ruokTkZCxIkM8V0iV4oseD"
            "TF28DBhIVdiplZWgYLO5IUAmVMChXycKkkydXiyS"
            "TAXcuKS/4Xl3+AeRoBACY9ywctFznlWYaL6EeZiB"
            "T4F9SPqoJK4J8Vv9rwayl6RZ+Jos+MJvFrCr3SRE"
            "6qDGLQpGakaBGWBiPRZGpmkxreamMNo4TB5UP+EP"
            "i0WpFiU3qRCKA5TKMQjwUB/2I/EjpjsqB2JPe03/"
            "7lm9VUiaVNv/BIJiHX6dT+eEusZs6ShjCrMjDJxq"
            "/shNvmdt3KkzfOefXYLemB/fPDlHTvV+8dol8zuf"
            "+knpNsUalYm1125W8Sb6BEIZvTO1c2avT8H+XgkT"
            "P7nv26jVZK6zYex2srDXh8P/Mu0UTcSuQyhLCpCx"
            "/ka3F+QtgrVyIdyxOSjEoDmjWFkrlcGAjfnOAaz6"
            "P0aAQ8f18yF2lEbIqgsvLGCDpsRCuzpTGjiPEtIF"
            "KRRpCicrRLrhYf8CUSkH69STBtOJE90chC2O4s1v"
            "uywrZLBFfQvmLsPPrQfls27XMfzex5fvf8W1Z32X"
            "uVFqXWoTLFqtsSc9a0+siveyyFPXUpd52HyqtNLs"
            "OVYKC5yk3tq3cGSdXqL9zaU9a5PJ2QStVaR2WpOT"
            "Z/d2fhr70Gz8X+3XFGPqAtsWn3KwPVXQmyDMnoEe"
            "K3EpfkIBEAGT0orCk+Bd46gnc5JRJKAHcRZK9AIr"
            "3n+VJwBEvBgxA3mIBw8qVeEBDS6QogAYmzo5Tc5M"
            "D7RgxgYFgjrrKLgOM9ajA5SoRNO6MqVvg4Y+Apub"
            "D72CqEGzJWvIlAKrPKwgK9ZGGcW6jJWGfU3h1Z8U"
            "TFtvq6LeVPLn/CG/C7n1wJ53V1myueWvaEB867Ay"
            "1DlZWLmktLmxdVVg61BCQly5+ED3qeWvFkxZYG+M"
            "PHVz3p8fvcT8Efbq2HP/x+YqgpGGwaSlQubCktbV"
            "ko4kSQp5249vVmMdcl1o/kS1wOAsTHDEeCcbcm81"
            "4fvsam8l7hmjGJC2Qd59FuumvyXpwhWUx9lcCZFx"
            "cL83ZHsRLFd11I7TPj/1GxdRr/X/GY06MknVqzTG"
            "bQkXa53/F4YQVZV3iGXF545jXhjcwyb9jN42+WNr"
            "jcDQGqjXVd+cbm7VsKb5JtW7ZvFvTWJOaREV4B9c"
            "jxpkBJEoEesEQY+ygmsA+N8c89gIDo+JX/R7KQrr"
            "jy7/A9+67eRj/FvAMYfilxmBAWnQWWGfio9Uiilm"
            "Ed16MdE4q3nXDQo8eLKorSu0luEF0uBSS0HK45wW"
            "fP0TW1CEK2sC1Ki8qTqO/oH1iCMlFc1MAbw0ir1y"
            "eAYMksZ2FfkeqjNThPxakMotqqmZKmmhm8mdy7Gg"
            "pnpsd7ULgOCC9mrfYNr0rNQe13Np5uiKgdOKBTNa"
            "/GlRzcsm3L/NT8xcnm1LoHVu54uSkqB+UZrV8yJz"
            "WYcaUWbtuzdWHqVNfuI82ZpCfdPbg40l5DvTv/tk"
            "B4e3/9zgVVLWKwxxHLesN1VeWJ2p6VTUN3hEMbOr"
            "tvHkl1ZTo12pJSlyPeHI63V8cqqrtXrazuqavxOv"
            "srg9lEuLTU5Jkr8FAreZVazzwl7vXkdAkukEIJIQ"
            "MIpD0pHonFx3mliXDCdSXuvpZ34zNUgjxLTig441"
            "xr9JZZbVGv0eiN2qxlXiO52+CJ2mwRr8HgjcIdj4"
            "EpK963RdxGoztim3GO8rKdV9+XukFnO4kUsZZ4TK"
            "ghRNvdLKCu8QbxBD7Or5xnRvvEV4IXOG8lWjbzli"
            "hi+WwHvpoFzdWBWyB0tCCQtA5Pr0RoFVOCK1/4Xh"
            "CzXj1fBjPV4hv8erha1ssaXjMHZIlsw7KVWGV1zE"
            "P73ErKiGqcIM2y4CxN2+0mQTuFQDIkxYQmEiXr9a"
            "sqhHxWsbKiM73m6LNf3bTp7LNH19TUoOPNm74Kx+"
            "mNFQv33ffs0iN/fHlDfOG+zzy39MiFl9a/03noxQ"
            "27nqhLMHqN1psaaOi+YbC8vH9Hc1V7VdgkV0lVCm"
            "lT3QsH1n9+fwv1i01nn7t7XTq97u7nvrp549ln71"
            "6bTq+9Gx627Pn7DyyKb+T/fHTZCw/ctCixefQvRz"
            "a9dEt7R7ZVqrE6bfHFB/vn3TJUoTHZ1YxMKp3TPr"
            "fz0BeFemt6iFrLfJeIgHU9TuAeiXmFEE2crDrigo"
            "l8haD0qhKo9siCZKxGSCeZcSqpUqw9MiPcmveYRf"
            "TGZ6aVH1WyOfW08qMKtAXd6RVgPpHlzQpxC93HK0"
            "TCET5UhhRe+tCO+rpwddDNak2MNX3L3MziBnejwS"
            "hX2xOpukC4tarktnSkui/Yva7RSV8MtGcCVGhOo8"
            "HC6lSxZCLQuDhduFllTFq8NpUp2lQZC4RLt1FsSS"
            "nCe8Rh2ki/TpiASkGC5KIJjjyfd6pwTVppErfEcp"
            "KgxaRmr7CT2pgmhU5NtFSGg00ocz7rxXuIqw1arf"
            "K0VqvTnGaYkobKmRcog428t0Rm1bBs4QO7RmstfC"
            "A3yl0aF3nvdW7gvcdkm0ROfUAwRGVxx3CxK5FEIe"
            "wTkeAOVTkJ7ugiISb2iaDOQwH2UXr349TW44UBMv"
            "y/6+kjmaYDeojFxIvX1wLdvWi957sFONfdi4bWjZ"
            "oNtiTzNYuxLqhBJmbJLAqgB4xLbxKZmvlwltXgJo"
            "RT1AEq2pjfA4vfHEjI2hvExd+W5coMXAvIYm83a8"
            "gjxYBvLWa57MfQCyTrw40cEV42CoHEyfL6OPid/5"
            "w+IBeQYe6FndtZh+oPj/r131M4HAv2186vNBvLu9"
            "NH/ikNMM7TJ247tPqIQ+kxFV6wk2mNrvAw+UtzpC"
            "EcygYNwFOT5I/Uamb9JE8lY0Kh6SRPZdN4aqL6JH"
            "88cADpjW66jvo16A1kh1oJcduLRIjLSyZ3v5gnd7"
            "+YP3z3y2wZz0lI2J1aenNX101LU/De2XXz0tQd7t"
            "re8lhPrdtd2xOLza11S+7suGkklRq5qaP9wEg6PX"
            "KgLTY34/Vm5sZifbVeb22fYD83Egsk9ZLvw5x1xK"
            "ope+lRwRpDY0ddgovtJFpUgCfBjrkMrRg9Kg5CGE"
            "mjRr1mchotuqcRdkTmtLiLnhbWFmpZIIQuM8KG/M"
            "k2XvR/XokVW3ktOHaM2nOcvL3wyeOFI+RNaI0tpB"
            "+nVkk/Dbh0PpErQ4i9AiiqSeQdgkLWYHCpMSqEGj"
            "Pled5jHsspPRPdkBIoWFtWgbohgZLlNGyOsXqyRY"
            "+lGXdBwim/YsufiXCJTqwqd5ELlepfO8p9pqZ4ok"
            "4nNxokCqZHd++W9HCj3xHLeJ61VZq/ZbHSj5drXO"
            "pfOdI1GWd1tiqmZUlSvWO7u3ZeZaSzpd7zBancYk"
            "b7TRfTJ6jVMKdGYgPBZRN5ZTEImzfjFc85E/moMD"
            "0n3ujr9ImbQ3TnOVuSrwaxSZjxxhA+gXYaVYHgmJ"
            "S441E1MiRmFi5xTgMfTIjdjlLJ6Z2OyBmTFjsHTO"
            "losrhWp3OrpEZ3uc9b6dOzZW2VIz0NVVV1arlBJ1"
            "Ey7bKK1sXJ6kUNvpvbO2OdK6vqFjeXS5bYpTaNz2"
            "U0eSMWT8ylXUo2plM1lRG9HmhRM5hxutIdkTvjxs"
            "Hm2iV1bkeyF/H425LnqHXF+mGJWNs8vX5YOVk/jN"
            "shoij/tx/R+n25P2lLLJLn/mEzF/6h0wo+zCGc99"
            "YQLkBXxcwZ0BSFntQpXoqWonsijWZPzp5JQxuKaA"
            "J3SULpbpRPM+B8mtQoXCTZ62fVQmG0dQ7eD726a1"
            "sxudbxmZG+vWUy6bJwtPBnxj2+avfBYoKtt7M6ER"
            "sJF34dQeOPXU0yadyvuUPUImqpkLLEq1CN0IciiT"
            "u+ac/zGi3K0+cJwfQSCZS0V6I0oLbYBQ4rEp85gL"
            "eqBlhgbmzOgRe20Y2/errw26e3vnhgjiQ5fGJT9s"
            "lLRuaPl4z03qbtJ9A4Pkl+i+SpS4SBiBNijRVwRX"
            "zDvdAQnlbhi8JbsefPFM0FT/ukLZrxeLOAhSN1TZ"
            "mojVoFJ3Z7JOv1ZKJWazQDzzpSeIy8TCiBZ20Ekn"
            "+5YAflTtxajYYZo+wHLbBOdZ63gFPmQUtb7hQS2X"
            "rUwIrDmU9R4sX6kDgVzqQm3Z0jBikrZS0+c6zcEm"
            "0Ml++1LR/y1S2oivYVXs8aZAqZz2l0GjTMZxaYuj"
            "K+6qCRxTJ1EPTQAumnQEP2EAAU8jJhpdKoCzcaoo"
            "wU9SFqD2e+Xns4dnp7uAxqtuVLp9gMKjwIH/z1rr"
            "sKqx6R7FRrbQrm62+Nb1uyhHyrfKlCK9Zz0POpt2"
            "CdGAg36vmFJKPoiwN3LGKlrQchMd4ICtmox/lgpV"
            "rodWg3TliZEgsoCxJV1QKxDNPtDClASEFB9FYM3d"
            "jTfeNgPD54Y3fPjUNrF40sXrRo8cgiSef9+9Eeof"
            "09qGlU+dBXN27fvnHjtq24NwtgQy9gQwMgwxAhVp"
            "dwymTeLxhPVEviJ9DGRK0+KDSxzFRnjDWZUJgOpa"
            "ubSdJMh0PBay89rjyt0ap1p5UlDcTVqw0usnLGhX"
            "dZA6myq1VOUsUaXJqSwl5bYS+gv9kvIxy2mSCYk8"
            "yXga92Igq48LRYR18q7p41pfiwXu89m+D8qXxMYL"
            "ormYuFEU9jJeCOyaR6EuDZhEDg3ZgfJgVI3wTBNS"
            "hP5ktFsCY09hQlgwuyOYczhhR5Kd4vxsfCcMOR5a"
            "QsV5m9Vm4me/UqJtq3mQNp3L4Npwo3/2bnXWBeJT"
            "vUOpviNrrTUOmdd+UVY4Wb/Cu58pO7dZqFuwvnDB"
            "Zq719oWnJJlLuKZQrd5WbmkNt76aDXQqrIXxbCrS"
            "p9Ps+qW/7HGLcKNcaIfreK9OsiniFyXULuFrd4ES"
            "mYD1d3IRKFBZsWrkaUCFdg2nXNoF33R9MOxYyy8I"
            "FS7PyWOmBd9UyhXik7aq6qbsZ+bbgV5NxThZr+8d"
            "IuuN/8oeTzfWxEMI2iQ0DRhE+gKNUqYAVjYzxRr5"
            "Wb2CJWWNI0FStch8oIQPy/jupMTUl1rQAgNDu2ez"
            "IAILqaGzzPS+VW0zxEd/AfgO6fZd4GDNFFDBMriX"
            "eJXBOi/IJULoqo3prKO2dCiJwJZREEjJFfNNJk1s"
            "S4uan8IoH685O5kUXooyO9wBllFt0WoQnJrUpwtv"
            "MoxYtwB+APLqHnh0CGlybzCwQZ7k3yqxEesWE8wg"
            "9VA+W7e0aQFC9AHgXBjyyCW93YwViZ/f8dr3zkIu"
            "gUAI3BgwCNlzWUtVaOdNdXVdWr5aweuNQhq2gDQL"
            "Ow0XsTAJquFVV1S5rKv0Z34MXyqjHuJv98zWKh78"
            "F4p8Rk9gDeKS/RjZCNNal0Ee8sqClxV3civDO/pX"
            "Zxvcue7L1y+sNWFEVUFB6gTRI3eNWNxB4il0Acyw"
            "p5n2CCt6JuC00Yuxh0uPU0qiSuVo2NyqujcrD5sC"
            "wU8mJXEgwQowbcDYKTow0jnMIwagUi48VhFbc5Zd"
            "kcIS/JConEJkkzmbIiz25yg4yluMeCnOGRVMS7Fn"
            "anJYZD6njn4ETrqLBaa5BVevuWbmscPLYhS3qMfl"
            "yfjFJHDmfCbwwNbB+orUr6n3pRXdFcXdm4oLGiM+"
            "n0BN1qVT9qMBUfukmiv6ZLGi30HpL5CFQh5bm2+5"
            "BtsvuQN4FSYARP2sSfqpjefWhyDxp9vT5Ep7/n85"
            "p/fK/Fzn53toZEMp/W9AOZ5vJ/Te1LNDk+M+H70O"
            "5Iflxe9pHdkehME11Pos6xH9In6cYfKw0GK/u9wr"
            "+zTsusTZPIMz9QauT0L9QKYso4pXiPZujacXomxx"
            "lOoGABjBOlDwPX0jGTsuL1GApLdbhX/PXIee6v+b"
            "8dU7sUd8kZueQuhUvdPxtVpYkf/UgmXSuRSKm1Ut"
            "nl9yeIK45ZegXGHCGqiH0zxxwtjpnzAShO8TbJ2G"
            "jI5kN91iRCoXcSgeV8RFBWEdwFSoyV4A5iEcSJKC"
            "ghNk8aKV9cLIiMoQI61Fo0fu3kr5PtvS4N/m4tn1"
            "NWOqc+bS8NkestsZZI6ZyGGlsgRA7PSoyq8rlpj9"
            "PjjIbKe2vccFAevPzepLxJRD66gCY+oEoDsXUmVf"
            "wTVHEluNpU3qggjDD7eBLXpWpxhyjUPAEpkZRJaD"
            "mHOufyCn8WN3ukSFzFkjLwctwHgEQtTkLXJcWELh"
            "ZJMOM3Ba6hyLsGT5nZFkWB8ajNEvYYskVCPMR6Iv"
            "GIx2DwRKy2Mg87jTAvFP/EGkafCF++V6QKlZ5xh5"
            "hCpxG8LlFMd991V6YY1EXmMJVABrQY1LWcz0cF0R"
            "HiunmvIDoolFsZxTnBoAHJjJfF9aLiwuZTKPDgtW"
            "Svv8SvCfJMgu8PWfSPeas7wmVdNR5PTVdZuKPauy"
            "xTGc9m45WZWTUAFQf33emsbCsLtyYcjkRruLy+vh"
            "z+AO9j+R0BsBz3EbARjxA5tbiPdFoTLK1RjdoKAx"
            "zWApk0yTwjV090xbIneIeQuT975//8GWfu1XFOEk"
            "futMJxUctJ9LzZcZGB91FGgssw0Ctn1o9qzKiA0o"
            "ReaYIzx8nXJIxcodaYzNN/PSaAUoBG8d8ECYGAdl"
            "ouMY+TTVTD+I+aqN1X/pAaP/eaKuQkB8ifr1a7dR"
            "M9t8gThadMDspH09Eur9ATq/AY7q02l7hdxK3RFO"
            "pQPFtPNSQPTYJP2pRCF5uykz8OIS6jOSAMcbXwMx"
            "BzwPK0KBijKxpL1XR2YYPblAKhqMlyXSyoE9yUbV"
            "ROlIan9sn6kK5sqNJgStJuuoMbpz5mu7ZFf35WX3"
            "R4vaIrbImVW8EVDt77+9PDH9XFbd0N0mzRMdZO9Z"
            "dvUn/ibVJGiP3dJHVgB1Ww3uZ9VEc1y0d1VEO7cH"
            "BLK3V2Rmc1csKMT+mxduVHE5Z7gvWitZ4+NutHd3"
            "uzfdTY7Nd0eyMnTPaUMY2/MN1Gi8OSJqba5eLYpD"
            "C2ko+mm+ujxua+Pt1mmu2p5Lsy01JPGe5U6yz2Ya"
            "wD+6MCNJYk9n/4iFEALp7Ks4IJKk3iZrYfOoNRNS"
            "5rspjwj9ygCEYEbBTKalgUoFVZZ/aaic2SwJ1yOp"
            "UjxEzTQ+avMTnFec+0N9ZrrAx19V1QKOdArhQEij"
            "vmlKhyQoW7j2mF/X/GMbHhAz0JPtkUm56QVv85UU"
            "wvvolbwFHESfjix7Gs6tB3Ir2Mf6IGx5nU51ETSz"
            "GqbpyQOvSdJ6dK28Jzk2J28Ydicznq6p/h5RjImh"
            "5n8HIs+m4D/m5zgmPP8yaxTYiJBTeNIpVZcZ/iDM"
            "lB0U92psSsPDddVCYeS9BX34fnHgGZQXs4o0RbcR"
            "cnrjFJcCGctzfikEhxb54S+I+y9SpQBUa8QQlt6P"
            "GZhL3+MKZZdnLOAB7amcz+7Tn5DG5fw+SLO9Cgr7"
            "mM1mkK8MQHuPe+D2EuvLeNBamncYd7kHqS0NEatF"
            "sB1VejPeLuJN58osPbwVmYhSuZY3W4fwHqaaDD22"
            "l1aP8JK2w7VJjGRA8B95KzCvmKaT0vfaxxsuslWg"
            "Cpd6jSU6TqK1u2fKXwwanxn58jVxwQel8eKJwmP1"
            "W4mXphH/XkZAtM6oXx/yl2wSxI9k3oIGYYcLZ5Cs"
            "qe7CSHNrNaUrwX0HXci7oYOwFdBwR0PdlgDuQmXy"
            "WgpSrcQbIY5yqdbD6HMHegCiW6I9nsx2hDdz20fW"
            "17us9dF2bP3rhuNpwt+Em4nx2sP5TPq0LWYpaOds"
            "nZOtqlxJzeqI4pE7pw/5NN7SYd1I/X3u6xCS3ysR"
            "rd0W+K9nD6HFPXmWP1bHNMT5lj4l+Z46Sl/HhzJL"
            "dPVWsfa57Uu1MMbHGuUjzX2uvMNTvbXOumzDX2L/"
            "Fzpt78eFN+c6Zm/ZiznmGn8bxhTaN5t6Hf5Lhm3l"
            "x9gitL8UlY1y3JermQZE3Dum6fSg5UN9AmrOQ2PR"
            "KAfI1wVjNJqg60xbGNNbyiswcqmPp/iVjXWeofj2"
            "YXrrv8Px7xvjubNpCIsuMSa8wGUBfda6mYSXC9qX"
            "yFYMFahN9+nEY+PgmHST3fAIcdcNgxSThUn9GQxJ"
            "37A8z/hmzXAT8fj3pf+0hY9PEUTOdHQibwv24nfi"
            "UpkdwA+IYwKsiMgrQqSJmCvJ3cUTi5llxDrl5b+B"
            "y5bW3h4cL9ZBu5nVyzrvAUevlc4ZF15LbCA8Ka/o"
            "yUYQpgiStgTW8V++RWFrkSmVzTfhPu2Ir0FiuuaZ"
            "SE4txZLs2+Jmcsdq0nhOM+cFED9K+0AP3tnmyWi7"
            "CvaFkiKLThZ8Qa1aLDhvPRsgnOhMK4g4sRMQibMc"
            "wbS0bo/CHq8/3fXbTQ5PNt5LbcgJmx6+zgRmu9e/"
            "7Xb7j13f5O6nTjoyOIG+8f+CxiTmuTSPSGzN4S61"
            "/WF379MqZ8suq420mq1pPh1479eC79WKwcSD9+Wz"
            "eJOHH793qRPce9CkG/24kA+h2Ma7sVls7WrTAodi"
            "vMmR0esXpr1o6Fk3Zqlt6F5yeN0vW6GDKrBUM0dZ"
            "zB2ccZmm2c4clx+j5snJO2ZpZxkl+YZliuN1bJzk"
            "ljUhyvFGcNZx1vbLbxlk+O1/WhdJ1pL2YZ9qVrjM"
            "P1Rz7NIGBbKIzfBeOvIFpQx4OZM0BQryGV9wjKLJ"
            "nEm7gmZ+RAxfCmifr4DBxmJufZiiIhpbB8zPIPm+"
            "WHa6xZ5lz4aPV0XSKMfbRKAr7i/okghybCe20HRd"
            "9EB0X/x++gOBnEn72XYv302MBsjRXp303BMZM9iv"
            "WEk+iZ0qM450T1UbR0LK9jCXBA0C/FFTdPIa5QbD"
            "LJ6UUHwzgmbKl1srhrdbGTy2zNir9xYZZmxYX3Ww"
            "48v3njCwfbC2NkvmJwT9unT4JO/8rV9yQnmB8S3c"
            "RRcWRuJFhRwBOtCaH2pgcPqFs5xnXrcTN5gwGnsl"
            "ArMLsOdZ3nVKgYvhG8icYErwJvoRe1wu0GeQrCMu"
            "cMbF7qDqdakTZuNOR0UdzAScXiHzRDhbVoz2Urm1"
            "PZxZ+7m/KjPSitJZm98l0y9RdTzOxXuu58fc+WJz"
            "ZUlrf2zymvWbBqQU3dxuPz151uxJXukWx/TUV30p"
            "FdvH5xXbxjfnvCkV3e0rlnsJz+x9aXDrU3rdqdbh"
            "xuT1VV+8rS8cruLfPmH1oS76wt9j5oDWW7q2MNi9"
            "pqBudkmrqrfG1pf2zx7YuvaEWs+h59H/Md8M1TgN"
            "nun9EJEdV+ozTv7O0Qm6e1QxQgXDUQs1qPO09c0w"
            "4RYbZq9KtHWkMUUbKJHVWYgwjUF0uSm3GjD282C8"
            "/6J4qRp+TZ/tn+iC9ONIe88E92SmT0uJnkla4ZHR"
            "On0jQLNH14lu6STdelaXoWmn5Yi8mOGS0mEUXrJi"
            "jKOVmuGeia/tfoOrXb5GQ93L/Qd/LkRAndP9uBkt"
            "5TLLkr0vUNoGsGvKrHZ9C1Eegauy5d26bRtRvTtR"
            "boWqvH+3+voSuq9KidIqut7CtA2Vh5chptK4C2bf"
            "8abZ3oN+r9CUo39bevP7bYHv7NC7+92WK806A/LL"
            "Wqh/5J0aX/QmoL72tvNxhuZxRXVk4X4CKdXwQ6tx"
            "MLiS/NoHPv9bqjzp9CYqSEQdC5THK0OtEqx9zhom"
            "DcF2HKd+jHRqMdhDyWbxc8uw6hszraeptBOpmZl0"
            "rla4Xc12Jk99vB1VMEE+bGXsSAWhari/n/orqY1f"
            "GT/dNMeNhZ1V5W2pSptAXLyJ32yrYInCStvdTIP8"
            "kPibFqsM7rDrhiFZWDdR5XwL30SsPMRqwSkS9P4Z"
            "qNfmIN8a0ZnGm7Hmd6pnIGvJQVqXxC2LY0lCz+4F"
            "UTYkkTYkm1sHOpSY9+uHByZXDDqJPjgHBzIJEfFo"
            "4ml8w6pN6jyHi2wYIZYHkF+gWAbgM/tAL1RiQwhw"
            "i+53/HtInw6xRuzYzHfyzu3T9zl5VrOteGZu65+h"
            "h83D5jF9bly9OV2fvX7NKa4Ov/19u5wLZ1Xgf4Xp"
            "KXD1F8k+KbIimJlESJFC8lUdTTepm2LMuR/IhlS2"
            "5jO1Is25ttLXbsOGk9zHEjzYvTrG7S1G06NFtSxx"
            "4vxWhGk7VAnQVYgibYsnjGgKFF0QwQsAFxii5bU8"
            "v7z/9fXj7Eh+wUAwyJ91LGveec/3H+/z/nO1IRni"
            "+2UpNA8r9/HjFyd1Lj5Ej54VCqjz9S3pM9gaClaG"
            "qM9Lex7Olkacjeiuy+g3y1I5TulBnT7kWmHetEdg"
            "tBbPsOmGX6cMn5IRgJOXvatmvmGe7hcXRnqDP2ID"
            "NOiSPrB5iDBt2xzQ2NW2IeT2xLY8PmmHsmfZR9v3"
            "OSRBUYjjgckeFA48awwxHe2Njc2dkMp9x47JS2St"
            "xo3h+npqgUyZVO9Ea4BvFKsg+sGeetCQhXCAXYzW"
            "bbdKK2D8LltkZSEyTilmExGxVZcQhZcUgLdYfBbr"
            "XoaoKFddF2KJypXgH2KTckuAKjQJvuI8GdnKUTs0"
            "aT9oYo3HHruSAL2+ETACZ1B7Ffq9BxektRO1WZ0+"
            "GJWUxcPmDL58/KCesV9aCbtCddyyPfUD+ef/KyN8"
            "qd3XFprltru/u0JLTtyEB8X59fqzZIW7yTjx5te+"
            "JmzDF55Ex3/PGdwb+d3d756JYAGFD0lEhe3TsZ6z"
            "0Qb6R1PU/s65a0nv56TaAWm+zQ9J65gaeW5hud04"
            "tTza46p0LeB4jd+VN9ezqtNZuObp+cqB09PrqTqe"
            "3cTDsHT4yHGh+aJyzo1ZcwC3oTdYwqxH1OtIRSXe"
            "RMvqsFbna1w5n85jQMGsh8ENISqCQriN4qOJPXMX"
            "Z/fUtkcAhvsnVB1aoIVNjlKKiWLezulABGV5U7jB"
            "eXRkkr9dJI9/UD+qnt7i44h6/WyXRSrdnNn8P7dl"
            "461FUUMq2QD45skXcXPIM/qxy/8GOyvsd8ZuTrAp"
            "85Sj2RRWgOitL0vwKY5o4imOYYj2lOMQZ3MAIjzF"
            "pQMxdw8xuWXJDBWl0nsjmzTLgfeLNcWCGUxziLfk"
            "4A8/l6OZ2lF/bL6GUJ9ELUkrTYI7FiimEZPpZsfY"
            "oRfPv7UcyPBKe+vGLo14TMmbRu3kK6YakuiIkRdO"
            "MTdINWnqkI0U2E1023oJsI1k0rr5se4CbADFQHC6"
            "A3kYZ8/masIz3SUX2ejmKCjnwM/j9ldJTrp68f/n"
            "09x0M/sR4UuNic5ZbffZ8Hg0t4fV3Bbamf2gm5wm"
            "U46BB2Nx5JRYiTF0dTyK68hhVEvkAH8eDIEig1iq"
            "+WfKPgroNeH0Z6HehAbc7gZshp1IOy0sVlEt7vp9"
            "XpyifHr6OfNpRNn6dE9/4XTQ7P4XwxFdXIx1rAHh"
            "0GywgBFxUk4IKjlDzVNjfwQiYMH33LfP2JL7oFtj"
            "P1MvrRlvcMSdFnSJQ4CSk7Ow2e8bLQE6eXhRy1Ly"
            "bJU0T3/gn9GMK1UnRUO5VUC/t4GvwMPSnrh54BVe"
            "Z0ahyIIcfdhtbwzxMbsnoB7LKGcpr33mXJtay2+8"
            "V2XkDxvV+iZ7djZoKZCgAdNpumnfAL/AQnaqNNUH"
            "UaGMJLFWYjckgp0j6pEA9TWLLg217CUmiGqBGMRd"
            "agzuwFCqnTT6JHCkRq5N9z5zej95a1ZSELv/sU5F"
            "pzG+w4du83EhfzGtWKvIgJvvJlJ/LuhvhK0cRZaE"
            "P9r02LK23JWS7A7zj2tUFteMbq9rXAsBXQJWqQ7o"
            "c6IXEhII/FsvyEIO2X+QG30Uejj+SDizZHzVWQ0E"
            "7Abjl+NAHjjPWdev3wzJuDxtNffdsrYcxzwcc69T"
            "bGtP/G9qu18qqDPTOb6jc8/daTB69/fWQ5NHGkZ/"
            "PJiRaxtn3roxu2zo/V0//+1avnRndPLt7sX+iUqa"
            "RmzYixRs04LBr1Bm1v+KlOVWWVdpj92vd+cvybKz"
            "/cPfj08vHtf7o7GD32+omm7Rt8kckzhH2MWe9XUU"
            "tgqW8UIUkjX4tzSmCUWvI6W+R4bEtYhCguHjCNWg"
            "KsVFmyTsnQppd8ag26X2fiExlSPrKF0JqBUHN1cP"
            "gpdxoCJCOsCINaXHiPYC2ami22CVCIWS35cM1CH8"
            "2HmGGN6xnWoBF+RxbFulaYDzHKOlgIZR3iUdZLGq"
            "a2AQ/TAs2aq4UprqEs1TrjGJXmW3szrlAp0rV4ji"
            "+zU1K2+i8pWz3IVlteNmGkLC3bOxlvppRsouHMfi"
            "SRDfJnPVQYKuNg2fxINpMgW7NYCN7x6vHBcCMvWy"
            "R9MGyEEPiUxlTlYIjXkgBBm/1pQU0M/qO1ghbYQi"
            "zKKf+7nPH6dElqudiT7YdM5jHM03JfxTbtBcLVGu"
            "o68tUAcJUIs0vBhm7Ui6PoopaQwDJWrkVeRy/pqr"
            "1aLkwLHZpFLUBJtATkojAFCwKNtTqa2wTKmr5wHy"
            "7dDlTF+nPpZnF0bcfGeztEV1ewrqLUFmq+kLbQ2B"
            "aPpBrITNjDpsO9s9QUJFNhUMt1oKsBcjWQ6SgQCt"
            "4RxFEd1cyDaamYc1ZaXYp1uGMlFfduOU9MTM1QP5"
            "N0Sb6L64QFqA3AdkwpSK4az5Dwh1JOvgxGE8BVgF"
            "6gVq4A3RPcBDUa+pMG8A5iPE0jf8dQl3c9I/747m"
            "WDN+iwB70mkxfSw7yG/GvR3OLi6u28BDLRb9dklE"
            "GfGbiXlMQlB9BIEaDaoJIR5ooHJSsEJ++HYaIdc2"
            "H4ir8wPJiQgwCZH14JSTmN6N6stDkVdU0wTpjIll"
            "vQhuxMO2OAWE9J9KZazFhX6DF03pyF5oryaEGhqh"
            "Z4ckYzTRoBqd3c6h/4y6H48Lkbx8cXY8NGr3v6B7"
            "1DYPexZ6Id5m7XyW76p1tOjJ0cb1iIH92KfrWN/W"
            "P83a+Bff210w7zv228eRqM7PWMuqyiHT88IJqKRW"
            "e+tToSbp/95tTL+4nPi/njaE4wI23M5hPILSKes4"
            "8x5N7SGPKaNDzBgvevDKAGHp/A48gTFl3CWRxKnp"
            "n5CuDJXxWmu4Kgcskb6WmunEyO+5UJ0OqcwRLj4e"
            "qJSj0SIiOTQ4cELioTLcx4hZDr8cxRXCGhxBfS81"
            "taJqgLXgc7Y7kyWXNk8pWWyZ9jpyWD1eaGVopNla"
            "jUJexZwllLCpcz5xWQ7+mcea6wiBezpjfom0TOK3"
            "h06aMuFWHio0Em0RWBcQZG6TAapTekhV6yiClhYZ"
            "KlgaX2SgW630TuN4VS7eRTRjGQygPVkoH70aRLSK"
            "HncuGu2Dp4+uXG7QLK+fPyg3Xhli4vv1wGPh6wzF"
            "EfqKLcsJa1izLUfow094QgnkVAmsMBqrk42FwsNO"
            "PiiHN5Bs1SEHYuOZwBtvC1V5hfo2VlDbWT8BcAHE"
            "vKTbnFACpP0CGhYgxaaohMLAtTsJ2vXgjxanaAKJ"
            "G6hRYtsZxbxynFfMUVXel6K8v9f3MkXWzlkQt5xV"
            "Zkg3dfGBXqFm7sX63PK7Yi5nV8Bem4Efleu7OZ8d"
            "AJOyMpB2mgLcTbMt9KmNiUh7Q7jxZ4aak2soAG18"
            "pjJpCZACm0Ug42X24jp7ilbpZvekVM+J/ranv77n"
            "0mn+Rrl18itcsThgjnla8kdGxSgeE2snQpc/lKSq"
            "W1MOkS62uqmmsNaPDCIUw40yCUkEeSWnxGozUo0F"
            "jAog8aRUCofM5ZMO1E6yUFeFS6JIWdDRi3+RhOaB"
            "USc1smh8jTBlGcQXpt9fN9b3z6/Ku0ZJf4uJAs9d"
            "yu1d+/+vynb+x7f/DMtcNz108PDJy+Pjd3/cwgH7"
            "G5cPa3fBbRfz/57NF3IVL29re3bfv27QsXbr84Pv"
            "7ibd5vl16RuJCGNiBP9CUq2QRa8kW4VjHwibA/Ci"
            "yubnQZZ1P9xibYUeznt1+JTxpGPmlYi70S1EES/b"
            "h7QBieVk280CgkWzQhfSSMOk7eAKET+qTSivN7tT"
            "rO5QFNGYHa4PI0wM1+3RKlDYNrn+jOPRQpcOIkHD"
            "jR2SeCRHF16TOnkf4zVw8987No5Kff2PH8oR6dFZ"
            "8yHR7s3TsQ1FdVtnp3zxxpP3OzI/ruxZnXnuh/++"
            "Klv1j0bjy8cfNcvEb0XyK5u2dPR+/+eIA4rmePHz"
            "mXOVgS9hIfPzN/FnzZGx98cGPqhYNtyJelhUMlMR"
            "/f58Q127qoXbkRfjDCRCNQuQ1iHptZvLdtusVVoy"
            "ZYrcXbcHoDjlVrILHceI97vXGApaMdCwcH/sc60t"
            "QKRQxK9peNdqTRMuiaaB7vPzZnagpreRALKazM4+"
            "40QnVhEE+XvemYM6/n7DaSejGS90WzaE1qo4aphC"
            "XEMUBvY5cqGAufm0+hZ9nxs2xkkWnDYaRpQCEspF"
            "S2cox1mb7obs+HhRaA2exGKofM+GW+e0VkE38k+j"
            "6pm5zHDswAA18RR0S28+fJ3zOflf97ZoL/+y5JJR"
            "2UuigL1UElq3C9PgUlhzHASk4RKleSapxHqYb0yQ"
            "1qNCjiQF014ZhRhApMgJtqsclYRbYng3TX1HhMzl"
            "Roq61qg0Xm+GOXQ/LPU6/HaxiVVF1hDLLRas+wX6"
            "myVjSHDxJZp8Sf02NSOXqXdgqNxClN5k1ktzg9eh"
            "OZHnOxMEFcVsm/iR5qGWjIm0QxaMfvI3ulwIv0B+"
            "mpCq3bgl/imMu2F95KElYrDUG2w+XZ6Fcqrcrm8P"
            "7w9I821jCVJIf7E3qM+vzB38O83vf4pbrCEOI1UQ"
            "mvcSA8jVWE5zlkG9Gj2DZhtC7G1kmEIryBcDYjW9"
            "RGcO4DoC4/+s6P8dh+N3znb1IQOAVvP3DBxWg9qU"
            "+EStiybr0mpjv2TnTwt4xmmf2Yy17c6uGizUEEbU"
            "F0HreFMHUQrACJvsQQiXoieRFbwFDqJif0SXcAvn"
            "Nb4Tt3HS85byfAKgSRN6RP1JdoO3XrNSUdVWqrLR"
            "oiNLrVIZcWb2XS4s0PZF/9RHQet7//J9nNf3jZi7"
            "ZsaYkmj/rex9Qn4g/Ev6AMGT6gjPABZdAFjWk+oI"
            "zwAWEiMRUEbX/cPbswtm1xtqdndnHb2MJst+iRbY"
            "uPdXc/trht28JMT8/MAvax99y7w5yiPuNzsLbyO3"
            "Y10Uj6tIqzNrNs+kC6QrnCU745F/ro4hOwlHjSpj"
            "hGo9OnKoyuehbzF6JlPOj86z1GT8huD8FOEvz2GE"
            "VqoxfwRvydoNco0mLwUchrNHpbMPjoeN4WlKPMNd"
            "LxRerP6F9jzrKHwrnZ9K2UVElVSnDBJcjQltK4Im"
            "kxwvLFdbOTsU2/g553B7P7aige8QjkPg1+JFamBp"
            "/taWNFsH3fWR+SD55Vc++O5CLzHhWn/ppKDoAtW9"
            "FSoHUA+kJrlyKQ1OLSFeieQwv3HGYFclHoAa0qkA"
            "hEUhQ5Um9hUxUKfK82kkapVhOSHHOLU8ZZlqsH55"
            "hN1uPIofpmgNjWY4gthUmaUCauWr+SrMbfV3sIUA"
            "75c62YGMc5KtJYybaIrxVYYjVtEVz/IxMfJJWREh"
            "dq2uSgzeQzrDCBlCxw0P01z9I0vXDZuP/S3//Rsz"
            "c39cm1ap2/7+Do5X/wiFoclrufo6/pXzitq4843/"
            "lWdGdPk8dWKa9UjG76lxdO/WRxUidqP6Sp1hoDxk"
            "PnPnxhbHbPsELtD/l//k5M7zI2VGmrNZ0610cfVX"
            "l87iqZPP7Yn4xfvv0MWYcNiKeZzcjXk1E+1KYIoE"
            "FhiQhaFLNpFDZwKqFSE/qIsbP+UEJ6i2NMaJGGSZ"
            "eMGOlPilUlBf0h5cmRv0ZO9BI+QlYivhzucT45Pg"
            "9K2HWcyoBLapM9Mwqn2REfB9CktC6/Jh26GYXqc1"
            "6/2KwboO/cOEd/94hOo7wmrZQwcvk1hU579LjiLZ"
            "Xb4K58W848dPjwqor+zapKtE+uWn1PbdOgf3S7Sn"
            "73r+iTW1dfczrpnSOrC2TsgkMAjUQDflxY59HB5y"
            "/ukFrv4l8xv0K9bZpKOmEdZsHsWFcEl3OgWdgdkk"
            "tW+GKSkM/vQOstE5t0OoQikg4yn1sqcBI/Z3WgVV"
            "WlHk6MYduT76uRtuwYMzjddNIeHtGKi753n+JO+h"
            "9ySUUmg9KqefNV0Ynl1RG+7HvsK9+f76uo+IpKTl"
            "9+sfX3MvHjQuV3LIOkgvlXqhrqH9spXEMPHCKHII"
            "Mey5BQsriSkB2LYSNi2DBx1WZBRrZjtrIdJLKR4q"
            "UQ8AC1hdTgkZPqtnK9juxpZyTClsPFXnOK2LfPvT"
            "K388rIzHL0WMP559BC4X94YaK7Xj4xpPpdOzMikr"
            "70bDRTxP7/AORzlvV42mNgZGBgYJRa8PGwLU88v8"
            "1XBnkOBhA4F6TcAaP/H//XxRHJHgnkcjAwgUQBXK"
            "AMLwAAeNpjYGRgYI/8l8TAwHHk//H/JzgiGYAiKO"
            "AlAKEQB2Z42m2TTWhTQRSFT+bn5SHhLYqIUAtCJS"
            "ISSgkiJQRBpNQQdFOkhlCki1KKvzViUEREJIgEKU"
            "IoMWjxB0FcPbJwUYqIGMSFutAsI7gQsVCQbqTI89"
            "zRSCx98HFm7ryZO3PPjFrBIfR8sVuAWkZHp9AwD7"
            "GfnPIOI2fvYzzWRkNN4ybJ6zSmODYdC3FCvXZaUO"
            "vRGmNZ8o7MkjNkz18VzhGZt6AMrgvSJnWypocw6N"
            "3BhD2LLTaF0F5C0fMQmsekwn6H/QRCVUJbV5G1c2"
            "iZHQjjRYQS96Ywab6gJWqTHMsiY55jwLbxiGv6fh"
            "KB3UV8+OYXJniOeY1ohVpk/gc6ybOXsNsEXKeMhl"
            "5Cnpozw8irEP1mG9JmDjWVwA3lRy3GG2w/9eZRkz"
            "jJmVkq5+gR1HQZB9Q6UoxfMdvR5+1EYHwMsB3oEG"
            "PMmyKSv8D8F7u1Z7tK9pEKcf8YDxe4t4BnG1VLmN"
            "RN98+C1N7FSthKz8bEE/UE4+QYY28kty1grxrCVf"
            "YrjF/WCe6viboNMeNoYpS1T7m6b0L8dPRVvHA+9K"
            "BK0VuueY+6Sn54R5Hu+rAR7uua80W86EW8oGemg2"
            "VX903wKjyveDH8PyoR/WT971I/kM/mJI7882EjrA"
            "v1oPOiF3rhPKP6r5hrlXede+K55C706WdA/DjQVX"
            "Web+QTGfkDvlPL1BmO0YsuXHuR92KR7yJDioL6hg"
            "wRlfv20b5HQeaqCqqkLusyPmhfcp8B27d5516g/z"
            "dWitM+eNpjYGDQgcM2hmOMZ5jeMG9iSWLpYjnC8o"
            "c1iXUJ6zs2HjYXtiS2Q+xs7FUcYhwpHM84Izg7uH"
            "S4lnH94y7insa9g/sOTw5vEO8UPi6+Er49fD/49f"
            "g38b8S4BDwEJgncERQTjBB8JAQj1CD0DPhJcJXRC"
            "RE/ERuiSqJJon2iB4QvSbGJ+YiFiPWJC4h3iT+TC"
            "JM4oykhpSb1ASpG9Im0l3SS2QkZNJk9slqyE6RvS"
            "U3QZ5BPki+Q6FO4Zgig6KekoNSlHKOChsQxqlKqW"
            "5Qc1LbpXZPfY76F40jmmGaGzRvaH7TOqT1QNtGu0"
            "v7i06NzhJdFl0P3Trdc3oCeil6u/SjDMQMmQwnGD"
            "4wMjHqM5YwXmNiY7LDNMV0kxmDWYTZPXMOczPzIv"
            "MrFk4WJyytLDdY6Vids66wnmWjZVNnc83WzPaQnY"
            "hdkj2b/QaHGEcBxztOJ5zXuXS4qrlucnNwu+E+xf"
            "2Y+w8PM49VnjaeJ7zMvJZ4K3hv84nw2eXr4LvKT8"
            "Svw1/Hv8b/XkBWwJ/ATUEWQVlB23DAI0GXgh4E/Q"
            "gWCA4LnhV8LyQm5FZoROiEMC4gdAqrCKsI1wvfFn"
            "4oQiWiCwCr/JuZAAEAAADpAGMABQAAAAAAAgABAA"
            "IAFgAAAQABewAAAAB42tVWTY/TZhAe70LJLgWpUo"
            "V6QMja06YN2WwLEgonVLQSEqIIENzaJraTWJvEwX"
            "Y23RXqqeeeEL+CA0d+AYIrP4Lf0BPimeed10k2pW"
            "V7QKoix+PX8/XMPDOJiHwlb2VdglMbIsHXIiYHUs"
            "OTk9fkfHDR5HV5EHxr8in5Jvjd5NNyN3hm8hc4f2"
            "fyGfk1eG9yTVprP5u8IZfW/jR58/TLtecmn5VWLT"
            "b5S4lrT00+F/xR+8vk83Jl84nJr+XC5guT30hr85"
            "X8KJlM5FBySaUvAykllG2JpI7799KSXVyXTdrFWR"
            "e6IbQO5S4sh9KRscQ4uS1T2cdTIUd4uonvVBK80X"
            "uEkxnuJfyHcg/nBa5cDqgRyh48jRn5DjyMcBrKFj"
            "x2cJZBatK/+kmgV5jVlJHVT0jPanVfbkEzlJ+ASX"
            "UXfS97aODkIa0LnGfU3UUkvXxkzbSPOIoyX8G8iP"
            "g43jbjOS/hMT/tqrK7H9X5+8xackWu4+kRK1hSZy"
            "zfWVYpzrS+mWEvIE2hoZh9nMvH4szjq3WIJ61ljr"
            "tWdkSdffrs/YeuN09s8W9vV7Mt6NnxKa2y1Ar08C"
            "YiK3pkVYoslLsxq6dsHxuTUtZ4zqEptEfGLD8Dnv"
            "mPWBONOkM1u8zVeVn2m3/CTGwvsTxkTIdG+b+Yx1"
            "aV5WKv65yMW2R3SfwJbV2NImaqddBuO+yar+uFq4"
            "xyxM3bBFJJ3pTw06ksIjKwx5gR342BUPVzsjPnXL"
            "vIbWZZ0u+A7BuyMlohZVMoj3GWMo4idExTvBPLVa"
            "NvcVsMbH6nVX0d/i78xraDEvmN0brQKmk1jxmzRh"
            "OiPVxCmtmuU3+K0zNe8yr+Mbabmb51V3t9RNzahQ"
            "fGnohaBd9G5l9rlbHCOWuVEXODbzp2FlUz6ep+wP"
            "xTYBvS7zJHp7CcMI/IGKjeFNMB33s9tS1tU/QYf0"
            "SM84lIGTOEfodvNbdDzpab14Q++hVnFOsNY9zAOO"
            "R3tuIYWS6+mgXtE26R1VkdIl7Gd7pr9o2/CdHHxo"
            "d5JDfR/qRjW93vhBkr8fH59pPcsAhJVVFlTZ+707"
            "E3xonLZMypCFm7IbOdWW3cbOh+zRfydHK8xIiC3E"
            "+xLXJGcr9afmOUZN6Q0fyWXe1dsdTdOeYOc0vYJT"
            "+ZzsvM+qneGsbq1Lg071zJnJyde5qywlMi8cz13S"
            "hMqwNWOu6US1zxnc04OWNa6HQMiGOC/bCDz4yfpu"
            "3p+W9Dk7tmBI2T6h/fvAXOFnfvLyu7d+ez/e/ZPj"
            "Ga+v/q39IqwgJ2WtUJN0CTFkPcM+7OHUTbg/f6Ci"
            "8+zU7/+3Q5jy5PzaLF38oxESWwD/G5hquF77ZcRR"
            "fbuK5Wnf3hA4W7A/x42m3RR2xTQRDG8f8kjp04vf"
            "fQe3vvOU6h29im994JJLENIcXBQGgB0atASNxAtA"
            "sgIKELBBwA0ZsoAg6c6eIAXMHhLTfm8tO3qx2NZo"
            "nib/1uYBX/q48gURItFqKxEIMVG7HEYSeeBBJJIp"
            "kUUkkjnQwyySKbHHLJI58CCimiHe3pQEc60ZkudK"
            "Ub3elBT3rRmz70pR8aOgYOinFSQilllNOfAQxkEI"
            "MZwlBcuBmGBy8+hjOCkYxiNGMYyzjGM4GJTGIyU5"
            "jKNKYzg5nMYjZzmMs85rOAConhGJvYzHUO8IEt7G"
            "EnBznBcbGyg3dsZL/YJJbdEsc2bvFe7BziJD/5wS"
            "+Ocpr73OUMC1nEXip5SBX3eMBTHvGYJ5E9VfOCZz"
            "znLH6+s4/XvOQVAT7zle0sJsgSllJDLYepo4F6Qj"
            "QSZhnLWcEnVkZ+oInVrGUNVzhCM+tYzwa+8I2rtN"
            "DKNd7wVuIlQRIlSZIlRVIlTdIlQzIlS7Ilh3Oc5x"
            "KXuc0FLnKHrZySXG5wU/Ikn11SIIVSZPXXNNUHdF"
            "u4NqhpmseMDjO6NKXH1G0o1b3bqSxv04i8V+pKQ+"
            "lQFiudyhJlqbJM+a+fy1RXfXXdXh30h0NVlRWNAf"
            "PI8Jk6fRZvOFTXFrxqDp/bnCOi8QezQJylAAAAeN"
            "pFzLkSwVAYxfHcRBbZN0vDREFzG4YHUEhmTBqjSm"
            "biNdQaJc/yRaX1ZBxcV3d+p/jf2fNM7KKUZO2qlr"
            "Fr3RYGryYU1iWle4xTPSKDN5VCWpaTxjfUyfKbdl"
            "D5B/obDwED0BsBEzDWAhZgLgS6gDX/gpEtsg5eO1"
            "R5qxVH0AWdvqQHuitJH/RmkgHoTyVDMBhKRmA4kI"
            "zB6F9OwHgrmYLJUrIHpuMfa0r5C0qYViQAAVIscw"
            "kAAA==) format('woff')"
        ),
    ),
)

SOURCE_URLS: Final[dict[str, str]] = {
    "html_tags.py": "https://github.com/googleprojectzero/domato/blob/fadff396cc45d521cc594d3e2396e27e887b1963/html_tags.py",
    "html.txt": "https://github.com/googleprojectzero/domato/blob/fadff396cc45d521cc594d3e2396e27e887b1963/rules/html.txt",
    "attributevalues.txt": "https://github.com/googleprojectzero/domato/blob/fadff396cc45d521cc594d3e2396e27e887b1963/rules/attributevalues.txt",
    "tagattributes.txt": "https://github.com/googleprojectzero/domato/blob/fadff396cc45d521cc594d3e2396e27e887b1963/rules/tagattributes.txt",
    "common.txt": "https://github.com/googleprojectzero/domato/blob/fadff396cc45d521cc594d3e2396e27e887b1963/rules/common.txt",
}

__all__ = [
    "ATTRIBUTE_VALUE_RULES",
    "COMMON_RULES",
    "HTML_RULES",
    "HTML_TAGS",
    "SOURCE_URLS",
    "TAG_ATTRIBUTE_RULES",
    "TableRule",
]
