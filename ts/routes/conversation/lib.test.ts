import { expect, test } from "vitest";

import { buildConversationCommand } from "./lib";

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

