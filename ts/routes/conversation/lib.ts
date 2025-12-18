// Copyright: Ankitects Pty Ltd and contributors
// License: GNU AGPL, version 3 or later; http://www.gnu.org/licenses/agpl.html

export type ConversationTurnResponse =
    | {
        ok: true;
        response: {
            assistant_reply_ko: string;
            follow_up_question_ko: string;
            micro_feedback?: { type: "none" | "correction" | "praise"; content_ko: string; content_en: string } | null;
            suggested_user_intent_en?: string | null;
            targets_used?: string[];
            unexpected_tokens?: string[];
        };
    }
    | { ok: false; error: string };

export function buildConversationCommand(
    kind:
        | "init"
        | "start"
        | "turn"
        | "gloss"
        | "event"
        | "wrap"
        | "apply_suggestions"
        | "plan_reply",
    payload?: unknown,
): string {
    if (kind === "init") {
        return "conversation:init";
    }
    if (kind === "gloss") {
        const lexeme = (payload as any)?.lexeme;
        if (typeof lexeme !== "string") {
            throw new Error("lexeme required");
        }
        return `conversation:gloss:${lexeme}`;
    }
    if (kind === "wrap") {
        return "conversation:wrap";
    }
    if (kind === "apply_suggestions") {
        const json = payload ? JSON.stringify(payload) : "{}";
        return `conversation:apply_suggestions:${json}`;
    }
    if (kind === "plan_reply") {
        const json = payload ? JSON.stringify(payload) : "{}";
        return `conversation:plan_reply:${json}`;
    }
    const json = payload ? JSON.stringify(payload) : "{}";
    return `conversation:${kind}:${json}`;
}

export type UiTokenKind = "word" | "space" | "punct";
export type UiToken = { text: string; kind: UiTokenKind };

export function tokenizeForUi(text: string): UiToken[] {
    if (!text) {
        return [];
    }
    const re = /[\w가-힣]+|\s+|[^\s\w가-힣]+/gu;
    const out: UiToken[] = [];
    for (const match of text.matchAll(re)) {
        const tok = match[0];
        let kind: UiTokenKind;
        if (/^\s+$/.test(tok)) {
            kind = "space";
        } else if (/^[\w가-힣]+$/.test(tok)) {
            kind = "word";
        } else {
            kind = "punct";
        }
        out.push({ text: tok, kind });
    }
    return out;
}
