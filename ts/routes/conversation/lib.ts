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
            word_glosses?: Record<string, string>;
        };
        debug_vocab?:
            | Record<
                string,
                { band?: string; r?: number | null; stage?: number | null }
            >
            | null;
    }
    | { ok: false; error: string };

export function buildConversationCommand(
    kind:
        | "init"
        | "decks"
        | "get_settings"
        | "set_settings"
        | "start"
        | "end"
        | "turn"
        | "turn_async"
        | "gloss"
        | "event"
        | "wrap"
        | "export_telemetry"
        | "apply_suggestions"
        | "plan_reply"
        | "plan_reply_async"
        | "translate"
        | "translate_async"
        | "poll",
    payload?: unknown,
): string {
    if (kind === "init") {
        return "conversation:init";
    }
    if (kind === "decks") {
        return "conversation:decks";
    }
    if (kind === "get_settings") {
        return "conversation:get_settings";
    }
    if (kind === "set_settings") {
        const json = payload ? JSON.stringify(payload) : "{}";
        return `conversation:set_settings:${json}`;
    }
    if (kind === "end") {
        return "conversation:end";
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
    if (kind === "export_telemetry") {
        const json = payload ? JSON.stringify(payload) : "{}";
        return `conversation:export_telemetry:${json}`;
    }
    if (kind === "apply_suggestions") {
        const json = payload ? JSON.stringify(payload) : "{}";
        return `conversation:apply_suggestions:${json}`;
    }
    if (kind === "plan_reply") {
        const json = payload ? JSON.stringify(payload) : "{}";
        return `conversation:plan_reply:${json}`;
    }
    if (kind === "translate") {
        const json = payload ? JSON.stringify(payload) : "{}";
        return `conversation:translate:${json}`;
    }
    if (kind === "translate_async") {
        const json = payload ? JSON.stringify(payload) : "{}";
        return `conversation:translate_async:${json}`;
    }
    if (kind === "plan_reply_async") {
        const json = payload ? JSON.stringify(payload) : "{}";
        return `conversation:plan_reply_async:${json}`;
    }
    if (kind === "poll") {
        const json = payload ? JSON.stringify(payload) : "{}";
        return `conversation:poll:${json}`;
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
