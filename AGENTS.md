# CLAUDE.md

Behavioral guidelines to reduce common LLM coding mistakes. Merge with project-specific instructions as needed.

**Tradeoff:** These guidelines bias toward caution and thoroughness over speed. Fixing a root cause takes longer than patching a symptom — that's intentional, not a mistake to correct later. For trivial tasks, use judgment.

## 1. Think Before Coding

**Don't assume. Don't hide confusion. Surface tradeoffs.**

Before implementing:
- State your assumptions explicitly. If uncertain, ask.
- If multiple interpretations exist, present them - don't pick silently.
- If a simpler approach exists, say so. Push back when warranted.
- If something is unclear, stop. Name what's confusing. Ask.

## 2. Simplicity First

**Minimum code that solves the problem. Nothing speculative.**

- No features beyond what was asked.
- No abstractions for single-use code.
- No "flexibility" or "configurability" that wasn't requested.
- No error handling for impossible scenarios.
- If you write 200 lines and it could be 50, rewrite it.

Ask yourself: "Would a senior engineer say this is overcomplicated?" If yes, simplify.

## 3. Fix Causes, Not Symptoms

**Don't patch what you tripped over. Fix what's actually wrong, everywhere it occurs.**

- Before fixing a bug, ask why it was possible - not just what value was wrong.
- If the same broken pattern exists elsewhere in the codebase, fix every instance. Don't patch the one you happened to be looking at and leave the rest.
- If a bug existed because nothing validated an input, enforced a type, or tested a path, close that gap. Correcting the one bad value you saw is not the fix.
- A `try/except` that swallows the error, a special case for one new input shape, or a config flag added to avoid choosing between two designs are symptom patches, not fixes - don't ship them as if they were.
- If the full fix is genuinely out of scope right now, say so explicitly and name what you're deferring. Don't narrow the fix silently and call it done.

Test: if the same bug could still happen tomorrow in a different file, the fix isn't finished.

## 4. Surgical Changes

**Touch only what you must. Clean up only your own mess. "Surgical" is about scope, not depth.**

When editing existing code:
- Don't "improve" adjacent code, comments, or formatting.
- Don't refactor things that aren't broken.
- Match existing style, even if you'd do it differently.
- If you notice unrelated dead code, mention it - don't delete it.

This doesn't conflict with §3: staying out of *unrelated* code is scope discipline; fixing the actual problem completely - including every place its root cause lives - is what you were asked to do. Don't confuse "don't touch what's unrelated" with "stop at the first symptom."

When your changes create orphans:
- Remove imports/variables/functions that YOUR changes made unused.
- Don't remove pre-existing dead code unless asked.

The test: every changed line should trace directly to the user's request or to the root cause behind it.

## 5. Documentation Stays Current

**If you change behavior, update what describes it. If you find stale docs, say so.**

- Comments, docstrings, and any README/doc text describing what you changed get updated in the same change - not a follow-up.
- If you notice existing documentation is wrong or outdated while working nearby, fix it or flag it explicitly. Don't leave known-stale docs behind because they weren't in scope.
- Document *why* and the *contract* (inputs, outputs, assumptions, edge cases) for anything non-obvious - not a line-by-line narration of what the code does.
- A change that works but leaves docs describing the old behavior is incomplete, not done.

## 6. Goal-Driven Execution

**Define success criteria. Loop until verified.**

Transform tasks into verifiable goals:
- "Add validation" → "Write tests for invalid inputs, then make them pass"
- "Fix the bug" → "Write a test that reproduces it, then make it pass"
- "Refactor X" → "Ensure tests pass before and after"

For multi-step tasks, state a brief plan:
```
1. [Step] → verify: [check]
2. [Step] → verify: [check]
3. [Step] → verify: [check]
```

Strong success criteria let you loop independently. Weak criteria ("make it work") require constant clarification.

---

**These guidelines are working if:** fewer unnecessary changes in diffs, fewer rewrites due to overcomplication, clarifying questions come before implementation rather than after mistakes, the same bug doesn't resurface in a different file next month, and no diff ever leaves documentation describing behavior that no longer exists.