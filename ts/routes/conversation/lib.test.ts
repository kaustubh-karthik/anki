// Copyright: Ankitects Pty Ltd and contributors
// License: GNU AGPL, version 3 or later; http://www.gnu.org/licenses/agpl.html

import { expect, test } from "vitest";

import { buildConversationCommand, tokenizeForUi } from "./lib";

test("buildConversationCommand init", () => {
    expect(buildConversationCommand("init")).toBe("conversation:init");
});

test("buildConversationCommand gloss", () => {
    expect(buildConversationCommand("gloss", { lexeme: "의자" })).toBe("conversation:gloss:의자");
});

test("buildConversationCommand turn", () => {
    expect(buildConversationCommand("turn", { text_ko: "의자", confidence: null })).toContain(
        "conversation:turn:",
    );
});

test("buildConversationCommand wrap", () => {
    expect(buildConversationCommand("wrap")).toBe("conversation:wrap");
});

test("tokenizeForUi splits Korean + punctuation + spaces", () => {
    const toks = tokenizeForUi("응, 거기에 있어.");
    expect(toks.map((t) => [t.text, t.kind])).toEqual([
        ["응", "word"],
        [",", "punct"],
        [" ", "space"],
        ["거기에", "word"],
        [" ", "space"],
        ["있어", "word"],
        [".", "punct"],
    ]);
});
