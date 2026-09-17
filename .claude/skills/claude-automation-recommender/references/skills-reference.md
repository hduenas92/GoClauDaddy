# Skills Recommendations

Skills are packaged expertise with workflows, reference materials, and best practices. Create them in `.claude/skills/<name>/SKILL.md`. Skills can be invoked by Claude automatically when relevant, or by users directly with `/skill-name`.

Some pre-built skills are available through official plugins (install via `/plugin install`).

**Note**: These are common patterns. Use web search to find skill ideas specific to the codebase's tools and frameworks.

---

## Available from Official Plugins

### Plugin Development (plugin-dev)

| Skill | Best For |
|-------|----------|
| **skill-development** | Creating new skills with proper structure |
| **hook-development** | Building hooks for automation |
| **command-development** | Creating slash commands |
| **agent-development** | Building specialized subagents |
| **mcp-integration** | Integrating MCP servers into plugins |
| **plugin-structure** | Understanding plugin architecture |

### Git Workflows (commit-commands)

| Skill | Best For |
|-------|----------|
| **commit** | Creating git commits with proper messages |
| **commit-push-pr** | Full commit, push, and PR workflow |

### Frontend (frontend-design)

| Skill | Best For |
|-------|----------|
| **frontend-design** | Creating polished UI components |

**Value**: Creates distinctive, high-quality UI instead of generic AI aesthetics.

### Automation Rules (hookify)

| Skill | Best For |
|-------|----------|
| **writing-rules** | Creating hookify rules for automation |

### Feature Development (feature-dev)

| Skill | Best For |
|-------|----------|
| **feature-dev** | End-to-end feature development workflow |

---

## Quick Reference: Official Plugin Skills

| Codebase Signal | Skill | Plugin |
|-----------------|-------|--------|
| Building plugins | skill-development | plugin-dev |
| Git commits | commit | commit-commands |
| React/Vue/Angular | frontend-design | frontend-design |
| Automation rules | writing-rules | hookify |
| Feature planning | feature-dev | feature-dev |

---

## Custom Project Skills

Create project-specific skills in `.claude/skills/<name>/SKILL.md`.

### Skill Structure

```
.claude/skills/
└── my-skill/
    ├── SKILL.md           # Main instructions (required)
    ├── template.yaml      # Template to apply
    ├── scripts/
    │   └── validate.sh    # Script to run
    └── examples/          # Reference examples
```

### Frontmatter Reference

```yaml
---
name: skill-name
description: What this skill does and when to use it
disable-model-invocation: true  # Only user can invoke (for side effects)
user-invocable: false           # Only Claude can invoke (for background knowledge)
allowed-tools: Read, Grep, Glob # Restrict tool access
context: fork                   # Run in isolated subagent
agent: Explore                  # Which agent type when forked
---
```

### Invocation Control

| Setting | User | Claude | Use for |
|---------|------|--------|---------|
| (default) | ✓ | ✓ | General-purpose skills |
| `disable-model-invocation: true` | ✓ | ✗ | Side effects (deploy, send) |
| `user-invocable: false` | ✗ | ✓ | Background knowledge |

---

## Custom Skill Examples

### API Documentation with OpenAPI Template

```yaml
---
name: api-doc
description: Generate OpenAPI documentation for an endpoint. Use when documenting API routes.
---
```

### Database Migration Generator

```yaml
---
name: create-migration
description: Create a database migration file
disable-model-invocation: true
allowed-tools: Read, Write, Bash
---
```

### Test Generator

```yaml
---
name: gen-test
description: Generate tests for a file following project conventions
disable-model-invocation: true
---
```

### PR Review with Checklist

```yaml
---
name: pr-check
description: Review PR against project checklist
disable-model-invocation: true
context: fork
---
```

### Project Conventions (Claude-only)

```yaml
---
name: project-conventions
description: Code style and patterns for this project. Apply when writing or reviewing code.
user-invocable: false
---
```

---

## Argument Patterns

| Pattern | Meaning | Example |
|---------|---------|---------|
| `$ARGUMENTS` | All args as string | `/deploy staging` → "staging" |

Arguments are appended as `ARGUMENTS: <value>` if `$ARGUMENTS` isn't in the skill.

## Dynamic Context Injection

Use `` !`command` `` to inject live data before the skill runs:

```yaml
## Current State
- Branch: !`git branch --show-current`
- Status: !`git status --short`
```

The command output replaces the placeholder before Claude sees the skill content.
