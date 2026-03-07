---
layout: post
title: "Setting Up Claude Code as a Context-Aware Development Collaborator"
date: 2026-03-07
categories: [software-engineering, ai, developer-tools]
tags: [claude-code, ai-assisted-development, developer-workflow]
---

![Views](https://hitscounter.dev/api/hit?url=https%3A%2F%2Fshbhmrzd.github.io%2Fsoftware-engineering%2Fai%2Fdeveloper-tools%2F2026%2F03%2F07%2Fsetting-up-claude-code-as-a-java-dev-collaborator.html&label=Views&icon=eye&color=%23007ec6&style=flat-square)

# Setting Up Claude Code as a Context-Aware Development Collaborator

I've been experimenting with **Claude Code**(Anthropic's terminal-based AI agent) to see how useful it can be as a coding assistant that actually understands the conventions and constraints of a codebase before it starts writing anything.

The biggest challenge I ran into was context. I didn't want the AI thinking about frontend CSS rules when I was debugging a JPA deadlock. And I didn't want to repeat myself every session about my local Postgres running on a specific port.

What I ended up finding most useful was the layered configuration system Claude Code provides. I'll use a Java/Spring Boot project as the running example here, but the layers themselves ie user-level settings, project-level config, and directory-scoped rules are language-agnostic. The same structure would apply whether you're working in Python, Go, Rust, or anything else.

Here is the setup I ended up with.

---

## The Configuration Layers

In my experience, the more context you dump into an AI session, the less focused the output gets. Claude Code handles this with three levels of configuration that stack on top of each other.

### User-Level Settings (~/.claude)

Before anything project-specific kicks in, Claude loads configuration from `~/.claude/` in your home directory. This is where I put personal preferences and environment details that apply across all my projects.

For example, my `~/.claude/CLAUDE.md` has things like:

- My preferred indentation style whether that's tabs or 4-space, this is where you would pin it down so the AI doesn't keep guessing or switching between the two.
- Explicit types over `var` in most cases.
- That I'm on macOS with Homebrew-managed JDKs.
- A reminder to always explain trade-offs when suggesting an approach.

I also have a few user-level rules in `~/.claude/rules/`. One tells Claude to never auto-commit to git without asking first, something I learned the hard way after it force-pushed to a feature branch during an early experiment.

This layer is useful because it follows you across projects. I don't have to repeat my personal setup in every repository's config.

### Project-Level CLAUDE.md

This is a markdown file at the root of your project. Claude reads it at the start of every session, so I keep it focused on things that apply everywhere in that specific codebase—build environment, stack info, coding standards.

For the Java/Spring project I was working on, the `CLAUDE.md` looked something like this:

- **Stack:** Java 21, Spring Boot 3.2, Gradle, PostgreSQL.
- **Standards:** We use Constructor Injection over `@Autowired`. Use Record types for DTOs.
- **Commands:** Build with `./gradlew build`, run tests with `./gradlew test`.

If this were a Python project, the same file would list your virtualenv setup, linting rules, and test runner. The point is to give Claude just enough to avoid the most common mistakes such as trying to run Maven commands on a Gradle project.

### Scoped Rules

While `CLAUDE.md` is global to the project, you can also define more specific rules in `.claude/rules/*.md`. These only get loaded when Claude is working in a matching directory.

For instance, I have a rule specifically for the persistence layer:

> **Location:** `.claude/rules/persistence.md`  
> **Rule:** Any new repository must extend `JpaRepository`. Never use `Optional.get()` without an `isPresent()` check or `orElseThrow()`. Use `@Query` only when QueryDSL becomes too verbose.

What I liked about this is that Claude only picks up the JPA rules when it's touching `/src/main/java/com/project/repository`. When it moves to the controller layer, it loads the controller-specific rules instead. This worked much better than putting everything into one massive file. The AI stays focused on what's relevant to the current task.

So the full layering ends up being: `~/.claude` (personal defaults) → project `CLAUDE.md` (team/project standards) → `.claude/rules/` (directory-specific constraints). Each layer narrows the focus.

---

## Skills and Agents

Claude Code also has two features for structuring more complex workflows: skills and sub-agents.

### Skills

Skills are basically reusable task definitions. They live in `.claude/skills/` and lay out a step-by-step plan for common tasks.

I built one for **API Versioning**. Instead of explaining the versioning strategy from scratch every time, the skill defines the steps:

1. Create a new package `v2` under the controller.
2. Copy the existing DTOs to the new package.
3. Update the `RequestMapping` to `/api/v2/...`.
4. Run the integration tests to ensure no regressions in `/api/v1/`.

This saved me from repeating the same instructions across sessions. I just point Claude at the skill, and it follows the steps. Useful for tasks that are common but have enough moving parts that you'll inevitably forget one.

### Sub-Agents

Sometimes you want a second pass with a different focus. Claude lets you define sub-agents in `.claude/agents/`, these are separate profiles with restricted tool access. The file name acts as the agent's identifier, so if you create `.claude/agents/security-reviewer.md`, you invoke it by just asking Claude to use it by name. Something like "run the security-reviewer agent on this code."

I set up a security review agent. It's configured with `context: fork`, so it doesn't see my previous 50 messages of trial-and-error. It only sees the final code.

- **Role:** Security-Reviewer.
- **Allowed Tools:** `read`, `grep`, `ls`.
- **Disallowed Tools:** `edit`, `write`, `bash`.

By removing write access, this agent can only read and report. It looks at my Spring Security configurations, flags open endpoints, and gives feedback. But it can't accidentally "fix" something and break something else. This ended up mimicking how we already work in teams: one person writes, another reviews.

---

## MEMORY.md

One of the more annoying parts of working with AI tools is having to repeat yourself. If I told it yesterday that my local Postgres is on port 5435 because 5432 is already in use, I don't want to say it again today.

Claude Code maintains a **`MEMORY.md`** file that acts as a running log of things it has learned across sessions.

- It automatically records build commands that worked.
- It notes down preferred testing flags.
- You can also edit this file manually. I added things like:  
  `- Local environment requires -Dspring.profiles.active=local for all gradle tasks.`

Claude reads the first 200 lines of this file at startup. Over time, it accumulates the kind of setup-specific knowledge that you'd normally have to explain to a new team member from scratch. This was probably the feature that made the biggest practical difference for me.

---

## Useful Shortcuts

A few things I found helpful while using the terminal interface:

- **`Shift + Tab`**: Toggles between Plan Mode and Act Mode. I usually let Claude plan first, review the steps, and then switch to Act. Helps catch bad ideas before they turn into bad code.
- **`/compact`**: Summarises the conversation when the history gets too long. Keeps token usage down and prevents the AI from getting confused by old context.
- **`/init`**: If you're setting up Claude on an existing project, this scans the codebase and helps generate that first `CLAUDE.md`. Saved me a fair bit of time on a legacy monolith.

---

## Final Thoughts

The goal of this setup wasn't to replace anyone on the team. It was to get the AI to a point where it already knows the conventions, the common workflows, and the environment quirks before it starts writing code.

I used Java/Spring Boot as the example because that's what I was working with, but none of this is Java-specific. The layered configuration of personal defaults, project standards and directory-scoped rules works the same way regardless of your stack. The underlying idea is the same one we use when onboarding a new developer: you give them coding guidelines, walk them through the standard procedures, and assign review responsibilities. Claude Code just lets you write that down in a way the AI ca

## Sources

- [Claude Code Overview — Anthropic Documentation](https://docs.anthropic.com/en/docs/claude-code/overview)
- [Claude Code Memory — Anthropic Documentation](https://docs.anthropic.com/en/docs/claude-code/memory)
- [Claude Code Sub-Agents — Anthropic Documentation](https://docs.anthropic.com/en/docs/claude-code/sub-agents)
- [Claude Code Skills — Anthropic Documentation](https://docs.anthropic.com/en/docs/claude-code/skills)
