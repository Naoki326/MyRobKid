# Evidence rules

When a conclusion depends on the behaviour of a **library, kernel, or upstream component** rather than on code this repo owns, these rules apply. They exist because diagnosing a hard bug without them has produced confident, wrong conclusions — see the origin note at the end.

The governing principle: **"I read the code" is not evidence that the shipped binary behaves that way.** Reading source proves what *some* copy of the implementation does. Three things routinely break the link between the two:

1. several variants of the same library exist, and the repo builds only one;
2. the copy you read is vendored/generated and not what this repo ships;
3. the code is ours but is not on the path that actually executes.

Close the gap before writing the conclusion, not after.

## 1. Confirm which implementation is actually built

Before citing kernel/library behaviour, resolve **which file this project compiles**.

- **ESP-IDF kernels and components ship variants.** `components/freertos/` contains both `FreeRTOS-Kernel/` and `FreeRTOS-Kernel-SMP/`; only one is linked. Check `firmware/sdkconfig` for the switch — e.g. `CONFIG_FREERTOS_SMP is not set` means the **non-SMP** sources are in play. Cite the one that is actually built: a citation from the wrong variant looks verified while pointing at code that never runs, which is worse than no citation at all.
- Generalise: for any dependency with build-time variants (SMP/unicore, TLS backends, `CONFIG_*`-selected implementations, board variants), find the selector and read it before reading the code.
- State the selector and its value in the conclusion, so the next reader can check it in one command.

## 2. `firmware/managed_components/` is upstream, untracked, and not a place to fix things

`firmware/managed_components/` is **gitignored** (`.gitignore`) and populated by the IDF component manager. Files there — e.g. `78__esp-ml307/src/web_socket.cc` — come from upstream and are **not tracked by this repo**.

- Reading them is fine and often necessary.
- **Do not propose edits there as fixes.** Such a change cannot be committed, and is wiped when the component manager re-syncs (version change, clean build), so "fixing" it silently does nothing.

  A **throwaway local log line**, added to observe behaviour and reverted afterwards, is a legitimate diagnostic — precisely because it is ephemeral and untracked. It is not a fix, and must never be described as one.
- The fix belongs in **this repo's own wrapper layer** instead — the file this repo owns that already wraps the upstream one. For the websocket case that is `firmware/main/protocols/websocket_protocol.cc` (tracked), which wraps the untracked `WebSocket` from `78__esp-ml307`.

  This repo currently has **no** patch-directory or fork mechanism for upstream components: `firmware/main/idf_component.yml` pins versions (`78/esp-ml307: ~3.6.6`) with no `git:` forks. So "patch the component" is not an available option here — don't propose it as one. If a genuine upstream bug needs fixing, that is a **project decision** (pin a fork, vendor the component, or report upstream), not something to slip into a diagnosis.
- Before editing *any* file under `firmware/`, confirm it is tracked: `git ls-files <path>`.

To see what this repo actually owns in the firmware tree:

```
git ls-files firmware/main | head
```

## 3. Verify the claim's weakest link, not its strongest

A hypothesis is usually supported by *some* plausible mechanism. That mechanism is not the thing to check — the **step that would make the whole chain false** is.

- Before writing a conclusion, name the single step that, if wrong, invalidates everything, and verify **that** one.
- Prefer a command whose output is a **fact** (`grep` for a config flag, `git ls-files`, a `#define`) over another layer of source reading. Reading more code rarely settles which of two plausible mechanisms is live.

## 4. Mark unconfirmed conclusions as unconfirmed

- Distinguish, in the text, between: (a) what a command or log **shows**, (b) what the code **implies**, and (c) what you **infer**. Do not let (c) inherit the tone of (a).
- An intermittent bug with no reproduction has **no confirmed** root cause, only ranked hypotheses. Write "leading hypothesis", and rank the alternatives — do not write "root cause".
- Never let a fix be committed on an unconfirmed hypothesis without saying so in the commit message. A plausible story is not a confirmed diagnosis; a specific, well-cited, wrong story is the most dangerous kind.

## 5. When the diagnosis is withdrawn

If a conclusion is later disproved, **correct the record where it was published** (the issue comment, the doc, the PR), naming the wrong claim and the corrected evidence. A retracted conclusion left standing is read as fact by everyone who comes after.

---

## Origin

Two consecutive diagnoses of issue #32 were built on mechanism stories that turned out not to exist — neither survived reading the kernel properly.

The first proposed a race between a task enqueueing and the main loop's clear-on-exit wakeup. The second "confirmed" it by citing a kernel file, presenting the mechanism as settled. Both were wrong, and the real lesson is about the **first** claim, not the citation: the race never existed, and one look at `xEventGroupSetBits` in either kernel (`vTaskSuspendAll` … `uxEventBits &= ~uxBitsToClear` … `xTaskResumeAll`, all inside the setter) would have shown it — the setter clears the bits itself, in its own critical section, so there is no window for a third party to slip into. No amount of additional source reading substitutes for checking that one step.

The same session also proposed a logging change inside gitignored `firmware/managed_components/`, which could never have been committed.

In both cases the failure was not missing knowledge — it was **treating "read some code" as "confirmed the shipped behaviour"**, and letting a well-cited story stand in for a checked one. These rules target exactly that.
