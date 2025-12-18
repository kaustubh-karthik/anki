<!--
Copyright: Ankitects Pty Ltd and contributors
License: GNU AGPL, version 3 or later; http://www.gnu.org/licenses/agpl.html
-->
<script lang="ts">
    import { onMount } from "svelte";
    import { bridgeCommand, bridgeCommandsAvailable } from "@tslib/bridgecommand";
    import {
        buildConversationCommand,
        tokenizeForUi,
        type ConversationTurnResponse,
    } from "./lib";

    let started = false;
    let deckOptions: string[] = [];
    let selectedDecks: string[] = ["Korean"];
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
    let showTranslateByTurn: Record<number, boolean> = {};
    let translationByTurn: Record<number, string> = {};
    let lastGloss: string | null = null;
    let error: string | null = null;
    let intentEn = "";
    let replyOptions: string[] = [];
    let applyDeck = "Korean";
    let showApplySuggestions = false;
    let lastWrap: any = null;
    let showPlanReply = false;
    let showSettings = false;
    let settings: any = null;
    let noGlossField = false;
    let lexemeFieldNamesText = "";
    let glossFieldNamesText = "";
    let showExportTelemetry = false;
    let telemetryJson = "";

    onMount(() => {
        if (!bridgeCommandsAvailable()) {
            return;
        }
        bridgeCommand(buildConversationCommand("get_settings"), (resp: any) => {
            if (resp?.ok) {
                settings = resp.settings ?? null;
                noGlossField = settings?.gloss_field_index == null;
                lexemeFieldNamesText = Array.isArray(settings?.lexeme_field_names)
                    ? settings.lexeme_field_names.join(", ")
                    : "";
                glossFieldNamesText = Array.isArray(settings?.gloss_field_names)
                    ? settings.gloss_field_names.join(", ")
                    : "";
            }
        });
        bridgeCommand(buildConversationCommand("decks"), (resp: any) => {
            if (!resp?.ok || !Array.isArray(resp.decks)) {
                return;
            }
            deckOptions = resp.decks.filter((d: unknown) => typeof d === "string");
            if (!selectedDecks.length && deckOptions.length) {
                selectedDecks = [deckOptions[0]];
            }
            if (deckOptions.length && !deckOptions.includes(applyDeck)) {
                applyDeck = deckOptions[0];
            }
        });
    });

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

    function toggleTranslate(index: number, textKo: string): void {
        error = null;
        showTranslateByTurn = toggleByIndex(showTranslateByTurn, index);
        if (translationByTurn[index] || !showTranslateByTurn[index]) {
            return;
        }
        if (!bridgeCommandsAvailable()) {
            return;
        }
        bridgeCommand(
            buildConversationCommand("translate", { text_ko: textKo }),
            (resp: any) => {
                if (!resp?.ok) {
                    error = resp?.error ?? "translate failed.";
                    return;
                }
                translationByTurn = {
                    ...translationByTurn,
                    [index]: resp.translation_en ?? "",
                };
            },
        );
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

    function repairMove(move: string, phraseKo: string): void {
        sendEvent({ type: "repair_move", move });
        if (!message.trim()) {
            message = phraseKo;
        }
    }

    function start(): void {
        error = null;
        if (!bridgeCommandsAvailable()) {
            error = "Bridge commands not available.";
            return;
        }
        if (settings?.provider === "fake") {
            error = "Provider is set to fake; choose local or openai in Settings.";
            return;
        }
        const decks = selectedDecks.filter(Boolean);
        if (!decks.length) {
            error = "Select at least one deck.";
            return;
        }
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
                showTranslateByTurn = {};
                translationByTurn = {};
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
                lastWrap = resp.wrap ?? null;
            }
        });
    }

    function endSession(): void {
        error = null;
        if (!bridgeCommandsAvailable()) {
            return;
        }
        bridgeCommand(buildConversationCommand("end"), (resp: any) => {
            if (!resp?.ok) {
                error = resp?.error ?? "end session failed.";
                return;
            }
            lastWrap = resp.wrap ?? null;
            started = false;
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

    function usePlannedReply(textKo: string): void {
        message = textKo;
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

    function exportTelemetry(): void {
        error = null;
        telemetryJson = "";
        if (!bridgeCommandsAvailable()) {
            return;
        }
        bridgeCommand(
            buildConversationCommand("export_telemetry", { limit_sessions: 50 }),
            (resp: any) => {
                if (!resp?.ok) {
                    error = resp?.error ?? "export telemetry failed.";
                    return;
                }
                telemetryJson = resp.json ?? "";
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
        <label for="decks">Decks</label>
        <select id="decks" multiple bind:value={selectedDecks} size="6">
            {#each deckOptions as d}
                <option value={d}>{d}</option>
            {/each}
        </select>
        <label for="topic">Topic</label>
        <input id="topic" bind:value={topicId} />
        <button on:click={start}>Start</button>
    </div>

    <div class="row">
        <button type="button" on:click={() => (showSettings = !showSettings)}>
            {showSettings ? "Hide" : "Show"} Settings
        </button>
    </div>
    {#if showSettings && settings}
        <div class="gloss">
            <div class="row">
                <label for="provider">Provider</label>
                <select id="provider" bind:value={settings.provider}>
                    <option value="local">local</option>
                    <option value="openai">openai</option>
                    <option value="fake">fake (no LLM)</option>
                </select>
                <label for="model">Model</label>
                <input id="model" bind:value={settings.model} />
            </div>
            <div class="row">
                <label>
                    <input type="checkbox" bind:checked={settings.safe_mode} />
                    safe_mode
                </label>
                <label for="redaction">Redaction</label>
                <select id="redaction" bind:value={settings.redaction}>
                    <option value="none">none</option>
                    <option value="minimal">minimal</option>
                    <option value="strict">strict</option>
                </select>
                <label for="max_rewrites">max_rewrites</label>
                <input
                    id="max_rewrites"
                    type="number"
                    bind:value={settings.max_rewrites}
                    min="0"
                    max="10"
                />
            </div>
            <div class="row">
                <label for="lexeme_field_index">lexeme_field_index</label>
                <input
                    id="lexeme_field_index"
                    type="number"
                    bind:value={settings.lexeme_field_index}
                    min="0"
                    max="50"
                />
                <label for="lexeme_field_names">lexeme_field_names</label>
                <input
                    id="lexeme_field_names"
                    bind:value={lexemeFieldNamesText}
                    placeholder="e.g. Front, Korean"
                />
                <label>
                    <input
                        type="checkbox"
                        bind:checked={noGlossField}
                        on:change={() => {
                            if (noGlossField) {
                                settings.gloss_field_index = null;
                            } else if (settings.gloss_field_index == null) {
                                settings.gloss_field_index = 1;
                            }
                        }}
                    />
                    no_gloss_field
                </label>
                <label for="gloss_field_index">gloss_field_index</label>
                <input
                    id="gloss_field_index"
                    type="number"
                    bind:value={settings.gloss_field_index}
                    min="0"
                    max="50"
                    disabled={noGlossField}
                />
                <label for="gloss_field_names">gloss_field_names</label>
                <input
                    id="gloss_field_names"
                    bind:value={glossFieldNamesText}
                    placeholder="e.g. Back, English"
                    disabled={noGlossField}
                />
                <label for="snapshot_max_items">snapshot_max_items</label>
                <input
                    id="snapshot_max_items"
                    type="number"
                    bind:value={settings.snapshot_max_items}
                    min="1"
                    max="50000"
                />
            </div>
            <div class="row">
                <button
                    type="button"
                    on:click={() => {
                        error = null;
                        const payload = {
                            ...settings,
                            max_rewrites: Number(settings.max_rewrites),
                            lexeme_field_index: Number(settings.lexeme_field_index),
                            lexeme_field_names: lexemeFieldNamesText
                                .split(",")
                                .map((s) => s.trim())
                                .filter(Boolean),
                            gloss_field_index:
                                settings.gloss_field_index == null
                                    ? null
                                    : Number(settings.gloss_field_index),
                            gloss_field_names: glossFieldNamesText
                                .split(",")
                                .map((s) => s.trim())
                                .filter(Boolean),
                            snapshot_max_items: Number(settings.snapshot_max_items),
                        };
                        bridgeCommand(
                            buildConversationCommand("set_settings", payload),
                            (resp: any) => {
                                if (!resp?.ok) {
                                    error = resp?.error ?? "failed to save settings";
                                }
                            },
                        );
                    }}
                >
                    Save Settings
                </button>
            </div>

            <div class="row">
                <button
                    type="button"
                    on:click={() => (showExportTelemetry = !showExportTelemetry)}
                >
                    {showExportTelemetry ? "Hide" : "Show"} Export telemetry
                </button>
                {#if showExportTelemetry}
                    <button type="button" on:click={exportTelemetry}>Export</button>
                {/if}
            </div>
            {#if showExportTelemetry && telemetryJson}
                <textarea rows="8" style="width: 100%;" readonly>
                    {telemetryJson}
                </textarea>
            {/if}
        </div>
    {/if}

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
                            toggleTranslate(idx, turn.assistant.assistant_reply_ko)}
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
                        {#if turn.assistant.suggested_user_intent_en}
                            <div>
                                Suggested intent (EN):
                                {turn.assistant.suggested_user_intent_en}
                            </div>
                        {/if}
                    </div>
                {/if}

                {#if showTranslateByTurn[idx]}
                    <div class="gloss">
                        {#if translationByTurn[idx]}
                            <div>{translationByTurn[idx]}</div>
                        {:else}
                            <div>(loading…)</div>
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

    <div class="row">
        <button
            type="button"
            on:click={() => repairMove("clarify_meaning", "무슨 뜻이에요?")}
        >
            Clarify meaning
        </button>
        <button
            type="button"
            on:click={() => repairMove("simplify", "좀 더 쉽게 말해 주세요.")}
        >
            Say it simpler
        </button>
        <button
            type="button"
            on:click={() => repairMove("confirm", "그러면 … 맞아요?")}
        >
            Confirm
        </button>
    </div>

    {#if lastGloss}
        <div class="gloss">{lastGloss}</div>
    {/if}
    {#if error}
        <div class="error">{error}</div>
    {/if}

    <div class="row">
        <button on:click={refreshWrap}>Refresh Wrap</button>
        <button type="button" on:click={endSession}>End session</button>
    </div>

    {#if lastWrap}
        <div class="gloss">
            <div>
                <strong>Strengths:</strong>
                {(lastWrap.strengths ?? []).join(", ")}
            </div>
            <div>
                <strong>Reinforce:</strong>
                {(lastWrap.reinforce ?? []).join(", ")}
            </div>
            {#if lastWrap.suggested_cards?.length}
                <div>
                    <strong>Suggested cards:</strong>
                    {lastWrap.suggested_cards.length}
                </div>
            {/if}
        </div>
    {/if}

    <div class="row">
        <button type="button" on:click={() => (showPlanReply = !showPlanReply)}>
            {showPlanReply ? "Hide" : "Show"} Plan my reply
        </button>
    </div>
    {#if showPlanReply}
        <div class="gloss">
            <div class="row">
                <label for="intent">English intent</label>
                <input
                    id="intent"
                    bind:value={intentEn}
                    placeholder="What do you want to say?"
                />
                <button on:click={planReply}>Generate</button>
            </div>
            {#if replyOptions.length}
                {#each replyOptions as opt}
                    <div class="row">
                        <button type="button" on:click={() => usePlannedReply(opt)}>
                            Use
                        </button>
                        <div>{opt}</div>
                    </div>
                {/each}
            {/if}
        </div>
    {/if}

    {#if lastWrap?.suggested_cards?.length}
        <div class="row">
            <button
                type="button"
                on:click={() => (showApplySuggestions = !showApplySuggestions)}
            >
                {showApplySuggestions ? "Hide" : "Show"} Add suggested cards to Anki
            </button>
        </div>
        {#if showApplySuggestions}
            <div class="gloss">
                {#each lastWrap.suggested_cards as sc}
                    <div>
                        <strong>{sc.front}</strong>
                        {#if sc.back}
                            — {sc.back}{/if}
                    </div>
                {/each}
                <div class="row">
                    <label for="applyDeck">Target deck</label>
                    <select id="applyDeck" bind:value={applyDeck}>
                        {#each deckOptions as d}
                            <option value={d}>{d}</option>
                        {/each}
                    </select>
                    <button on:click={applySuggestions}>Apply</button>
                </div>
            </div>
        {/if}
    {/if}
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
