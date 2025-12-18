<!--
Copyright: Ankitects Pty Ltd and contributors
License: GNU AGPL, version 3 or later; http://www.gnu.org/licenses/agpl.html
-->
<script lang="ts">
    import { bridgeCommand, bridgeCommandsAvailable } from "@tslib/bridgecommand";
    import {
        buildConversationCommand,
        tokenizeForUi,
        type ConversationTurnResponse,
    } from "./lib";

    let started = false;
    let decksText = "Korean";
    let topicId = "room_objects";
    let message = "";
    type Confidence = "confident" | "unsure" | "guessing" | null;
    let confidence: Confidence = null;
    type AssistantResponse = NonNullable<
        Extract<ConversationTurnResponse, { ok: true }>["response"]
    >;
    type Turn = {
        user_text_ko: string;
        confidence: Confidence;
        assistant: AssistantResponse;
    };
    let turns: Turn[] = [];
    let showHintByTurn: Record<number, boolean> = {};
    let showExplainByTurn: Record<number, boolean> = {};
    let showIntentByTurn: Record<number, boolean> = {};
    let lastGloss: string | null = null;
    let error: string | null = null;
    let intentEn = "";
    let replyOptions: string[] = [];
    let applyDeck = "Korean";

    function sendEvent(payload: Record<string, unknown>): void {
        if (!bridgeCommandsAvailable()) {
            return;
        }
        bridgeCommand(buildConversationCommand("event", payload));
    }

    function toggleByIndex(
        map: Record<number, boolean>,
        index: number,
    ): Record<number, boolean> {
        return { ...map, [index]: !map[index] };
    }

    function practiceTargets(index: number): void {
        const turn = turns[index];
        const targets = turn?.assistant?.targets_used ?? [];
        for (const itemId of targets) {
            if (itemId.startsWith("lexeme:")) {
                sendEvent({
                    type: "practice_again",
                    token: itemId.slice("lexeme:".length),
                });
            }
        }
    }

    function markConfusingMessage(index: number): void {
        sendEvent({ type: "mark_confusing_message", turn_index: index + 1 });
    }

    function start(): void {
        error = null;
        if (!bridgeCommandsAvailable()) {
            error = "Bridge commands not available.";
            return;
        }
        const decks = decksText
            .split(",")
            .map((s) => s.trim())
            .filter(Boolean);
        bridgeCommand(
            buildConversationCommand("start", { decks, topic_id: topicId || null }),
            (resp: { ok: boolean; error?: string }) => {
                if (!resp?.ok) {
                    error = resp?.error ?? "Failed to start session.";
                    return;
                }
                started = true;
                turns = [];
                showHintByTurn = {};
                showExplainByTurn = {};
                showIntentByTurn = {};
            },
        );
    }

    function send(): void {
        error = null;
        if (!started) {
            error = "Start a session first.";
            return;
        }
        const text = message.trim();
        if (!text) {
            return;
        }
        message = "";
        bridgeCommand(
            buildConversationCommand("turn", { text_ko: text, confidence }),
            (resp: ConversationTurnResponse) => {
                if (!resp?.ok) {
                    error = resp?.error ?? "Turn failed.";
                    return;
                }
                turns = [
                    ...turns,
                    {
                        user_text_ko: text,
                        confidence,
                        assistant: resp.response,
                    },
                ];
            },
        );
    }

    function gloss(lexeme: string): void {
        if (!bridgeCommandsAvailable()) {
            return;
        }
        bridgeCommand(buildConversationCommand("gloss", { lexeme }), (resp: any) => {
            if (resp?.found) {
                lastGloss = `${resp.lexeme}: ${resp.gloss ?? ""}`.trim();
            } else {
                lastGloss = null;
            }
        });
    }

    function refreshWrap(): void {
        if (!bridgeCommandsAvailable()) {
            return;
        }
        bridgeCommand(buildConversationCommand("wrap"), (resp: any) => {
            if (resp?.ok) {
                // show a minimal summary in the footer
                lastGloss = `reinforce: ${(resp.wrap?.reinforce ?? []).join(", ")}`;
            }
        });
    }

    function planReply(): void {
        error = null;
        replyOptions = [];
        const intent = intentEn.trim();
        if (!intent) {
            return;
        }
        bridgeCommand(
            buildConversationCommand("plan_reply", { intent_en: intent }),
            (resp: any) => {
                if (!resp?.ok) {
                    error = resp?.error ?? "plan-reply failed.";
                    return;
                }
                replyOptions = resp.plan?.options_ko ?? [];
            },
        );
    }

    function applySuggestions(): void {
        error = null;
        bridgeCommand(
            buildConversationCommand("apply_suggestions", { deck: applyDeck }),
            (resp: any) => {
                if (!resp?.ok) {
                    error = resp?.error ?? "apply suggestions failed.";
                    return;
                }
                lastGloss = `created notes: ${(resp.created_note_ids ?? []).join(", ")}`;
            },
        );
    }
</script>

<div class="page">
    <h1>Conversation Practice</h1>
    {#if !bridgeCommandsAvailable()}
        <p>Not running inside Anki.</p>
    {/if}

    <div class="row">
        <label for="decks">Decks (comma-separated)</label>
        <input id="decks" bind:value={decksText} />
        <label for="topic">Topic</label>
        <input id="topic" bind:value={topicId} />
        <button on:click={start}>Start</button>
    </div>

    <div class="chat">
        {#each turns as turn, idx}
            <div class="msg user">{turn.user_text_ko}</div>

            <div class="msg assistant">
                <div class="assistantText">
                    <div>
                        {#each tokenizeForUi(turn.assistant.assistant_reply_ko) as tok}
                            {#if tok.kind === "word"}
                                <button
                                    type="button"
                                    class="tok"
                                    aria-label={`token ${tok.text}`}
                                    on:mouseenter={() => gloss(tok.text)}
                                    on:click={() =>
                                        sendEvent({
                                            type: "dont_know",
                                            token: tok.text,
                                        })}
                                    on:dblclick={() =>
                                        sendEvent({
                                            type: "practice_again",
                                            token: tok.text,
                                        })}
                                    on:contextmenu={(e) => {
                                        e.preventDefault();
                                        sendEvent({
                                            type: "mark_confusing",
                                            token: tok.text,
                                        });
                                    }}
                                >
                                    {tok.text}
                                </button>
                            {:else}
                                <span>{tok.text}</span>
                            {/if}
                        {/each}
                    </div>
                    <div>{turn.assistant.follow_up_question_ko}</div>
                </div>

                <div class="row">
                    <button
                        type="button"
                        on:click={() =>
                            (showHintByTurn = toggleByIndex(showHintByTurn, idx))}
                    >
                        Hint
                    </button>
                    <button
                        type="button"
                        on:click={() =>
                            (showExplainByTurn = toggleByIndex(showExplainByTurn, idx))}
                    >
                        Explain
                    </button>
                    <button
                        type="button"
                        on:click={() =>
                            (showIntentByTurn = toggleByIndex(showIntentByTurn, idx))}
                    >
                        Translate
                    </button>
                    <button type="button" on:click={() => practiceTargets(idx)}>
                        Practice this
                    </button>
                    <button type="button" on:click={() => markConfusingMessage(idx)}>
                        Mark confusing
                    </button>
                </div>

                {#if showHintByTurn[idx]}
                    <div class="gloss">
                        <div>
                            targets_used: {(turn.assistant.targets_used ?? []).join(
                                ", ",
                            )}
                        </div>
                        {#if (turn.assistant.unexpected_tokens ?? []).length}
                            <div>
                                unexpected_tokens: {(
                                    turn.assistant.unexpected_tokens ?? []
                                ).join(", ")}
                            </div>
                        {/if}
                    </div>
                {/if}

                {#if showExplainByTurn[idx]}
                    <div class="gloss">
                        {#if turn.assistant.micro_feedback}
                            <div>{turn.assistant.micro_feedback.content_ko}</div>
                            {#if turn.assistant.micro_feedback.content_en}
                                <div>{turn.assistant.micro_feedback.content_en}</div>
                            {/if}
                        {:else}
                            <div>(no feedback)</div>
                        {/if}
                    </div>
                {/if}

                {#if showIntentByTurn[idx]}
                    <div class="gloss">
                        {#if turn.assistant.suggested_user_intent_en}
                            <div>{turn.assistant.suggested_user_intent_en}</div>
                        {:else}
                            <div>(no translation)</div>
                        {/if}
                    </div>
                {/if}
            </div>
        {/each}
    </div>

    <div class="row">
        <select bind:value={confidence}>
            <option value={null}>confidence: (none)</option>
            <option value="confident">confident</option>
            <option value="unsure">unsure</option>
            <option value="guessing">guessing</option>
        </select>
        <input
            placeholder="Type Korean…"
            bind:value={message}
            on:keydown={(e) => e.key === "Enter" && send()}
        />
        <button on:click={send}>Send</button>
    </div>

    {#if lastGloss}
        <div class="gloss">{lastGloss}</div>
    {/if}
    {#if error}
        <div class="error">{error}</div>
    {/if}

    <div class="row">
        <button on:click={refreshWrap}>Refresh Wrap</button>
    </div>

    <div class="row">
        <label for="intent">English intent</label>
        <input
            id="intent"
            bind:value={intentEn}
            placeholder="What do you want to say?"
        />
        <button on:click={planReply}>Plan Reply</button>
    </div>
    {#if replyOptions.length}
        <div class="gloss">
            {#each replyOptions as opt}
                <div>{opt}</div>
            {/each}
        </div>
    {/if}

    <div class="row">
        <label for="applyDeck">Apply suggestions to deck</label>
        <input id="applyDeck" bind:value={applyDeck} />
        <button on:click={applySuggestions}>Apply</button>
    </div>
</div>

<style>
    .page {
        padding: 16px;
        font-family: system-ui, sans-serif;
    }
    .row {
        display: flex;
        gap: 8px;
        align-items: center;
        margin: 8px 0;
    }
    .chat {
        border: 1px solid #ddd;
        padding: 8px;
        min-height: 200px;
        margin: 12px 0;
    }
    .msg {
        margin: 6px 0;
        white-space: pre-wrap;
    }
    .msg.user {
        text-align: right;
    }
    .tok {
        border: 0;
        background: transparent;
        padding: 0;
        margin: 0;
        font: inherit;
        cursor: pointer;
        color: inherit;
        text-decoration: underline;
        text-underline-offset: 2px;
    }
    .gloss {
        margin-top: 8px;
        color: #333;
        font-size: 0.9em;
    }
    .error {
        margin-top: 8px;
        color: #b00020;
    }
</style>
