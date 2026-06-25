# Changelog

## [0.1.1](https://github.com/kaiitunnz/waypoint/compare/v0.1.0...v0.1.1) (2026-06-25)


### Bug Fixes

* don't let build-generated files block waypointctl update ([#167](https://github.com/kaiitunnz/waypoint/issues/167)) ([a11530f](https://github.com/kaiitunnz/waypoint/commit/a11530f686b0bd5b5e0bc5b3fee7a8df1c585acd))

## 0.1.0 (2026-06-24)


### Features

* add CI, release-please versioning, and a bootstrap installer ([#137](https://github.com/kaiitunnz/waypoint/issues/137)) ([4134c02](https://github.com/kaiitunnz/waypoint/commit/4134c0251d0dbb76b2251f27445c18ad482c8130))
* add claude_tty backend plugin (Phase 1 MVP) ([87a683c](https://github.com/kaiitunnz/waypoint/commit/87a683cf85f531f043d57a941552ba329810d392))
* add CLI to upload session attachments ([#160](https://github.com/kaiitunnz/waypoint/issues/160)) ([9715858](https://github.com/kaiitunnz/waypoint/commit/9715858b337ac8afc38076e3c450786ee814ae8b))
* add orchestration-focused CLI ergonomics ([fa7a197](https://github.com/kaiitunnz/waypoint/commit/fa7a197ed88c6f1d4f66eb4af32212f1995cd53f))
* add recursive help command to the waypoint CLI ([407d147](https://github.com/kaiitunnz/waypoint/commit/407d1474077f316f22f306d65f4ac5aa804b187b))
* add recursive help command to waypointctl ([2a4c05f](https://github.com/kaiitunnz/waypoint/commit/2a4c05f16cfadb39f32c643539fb44dcca281e99))
* add sessions set-permission-mode command ([b6a41d4](https://github.com/kaiitunnz/waypoint/commit/b6a41d4b3e0c042cc1a44ce0a32041f238f268c6))
* auto-accept workspace-trust prompt for claude_tty sessions ([9243f00](https://github.com/kaiitunnz/waypoint/commit/9243f00e4028ba79c630b9bd484833eba71b7955))
* bring claude_tty to capability parity with claude_code ([3b3ddf9](https://github.com/kaiitunnz/waypoint/commit/3b3ddf98dc232cfc19a940050166de36174557e8))
* clean up worktree branches on session delete and reap ([f006a58](https://github.com/kaiitunnz/waypoint/commit/f006a5831a57acb85033787829663820863b1943))
* fold Task tool stream into TodoWrite snapshots in claude_tty normalizer ([f04ae43](https://github.com/kaiitunnz/waypoint/commit/f04ae437a965755ced27991cd0c6b8a81069eb43))
* let claude_tty change settings on a running session ([b034a48](https://github.com/kaiitunnz/waypoint/commit/b034a4811d0fe225d668597b7b77a4640b0571e7))
* make the composer interrupt an always-available lead-cluster control ([57d616f](https://github.com/kaiitunnz/waypoint/commit/57d616f7e4d109e21ec24b24925da921a6c2bc68))
* open transcript filesystem paths in the workspace panel ([#158](https://github.com/kaiitunnz/waypoint/issues/158)) ([e3a1d12](https://github.com/kaiitunnz/waypoint/commit/e3a1d129de9df364821414bdfe6b792fefefac52))
* redesign the session composer as a command-bar capsule ([783c23f](https://github.com/kaiitunnz/waypoint/commit/783c23fa57fe3f7a5ef81223127144a1d4b9b8ca))
* send follow-up messages while the agent is busy ([efbfd6c](https://github.com/kaiitunnz/waypoint/commit/efbfd6cf56564c56bf9e3c648a5575e4dc58f1ef))
* surface AskUserQuestion prompts in claude_tty sessions ([3d41831](https://github.com/kaiitunnz/waypoint/commit/3d41831f6de9437c8a3133027a0ecceb73e6c10e))
* surface Claude TUI slash commands in claude_tty autocomplete ([7322551](https://github.com/kaiitunnz/waypoint/commit/73225515546bcfe668b18cf4e349353a36cb9e08))
* syntax highlighting and refresh controls in workspace preview ([#159](https://github.com/kaiitunnz/waypoint/issues/159)) ([765fe7d](https://github.com/kaiitunnz/waypoint/commit/765fe7d85b92d58720f9070e62e7fe54b8fec071))
* warn on an unknown --model at session start ([28119f7](https://github.com/kaiitunnz/waypoint/commit/28119f7865301eeb1b1ff377a3c33a8eb5bb9a5c))
* wire live approval detection and keystroke dispatch for claude_tty ([c156b70](https://github.com/kaiitunnz/waypoint/commit/c156b705576d90bca564696662ae1f29c03e4a55))
* workspace file side-dock and full-page browser ([#161](https://github.com/kaiitunnz/waypoint/issues/161)) ([09399e7](https://github.com/kaiitunnz/waypoint/commit/09399e7e234c255bb8ad26438ac361af527b37b1))


### Bug Fixes

* avoid replaying the transcript when resuming a claude_tty thread ([c6b186c](https://github.com/kaiitunnz/waypoint/commit/c6b186cc77c7a3233a11e50ffad26c81660628e6))
* claim a claude_tty approval before its keystroke await ([7033cf2](https://github.com/kaiitunnz/waypoint/commit/7033cf2f979cf7b81bf3f3791ff338ab4d9fe5bf))
* clarify waypoint CLI skills and permission inheritance ([7f50d06](https://github.com/kaiitunnz/waypoint/commit/7f50d06dd55230cb66038d0abc1ac612de8ea627))
* classify AskUserQuestion dialogs that have no inline free-text option ([c4ecf7d](https://github.com/kaiitunnz/waypoint/commit/c4ecf7dd2296ad04506e0b269c4b3857d8c21f93))
* classify single-question AskUserQuestion dialogs ([bc328ef](https://github.com/kaiitunnz/waypoint/commit/bc328ef14bda421a787108b2b28be0c0d0ba298f))
* coerce non-serializable option defaults in recursive help ([fdba289](https://github.com/kaiitunnz/waypoint/commit/fdba289a3296477f3db3a44bb786bdc4b1c67c79))
* declare click as a direct waypointctl dependency ([74e37a2](https://github.com/kaiitunnz/waypoint/commit/74e37a228af14a908cb74bf3b32a741db59d0d30))
* detect a dead tmux pane in describe_target ([badff75](https://github.com/kaiitunnz/waypoint/commit/badff756ca8223a99a1b2af691184a4d767f2305))
* detect claude_tty approvals regardless of stored permission mode ([1a7615f](https://github.com/kaiitunnz/waypoint/commit/1a7615fc1134c0676f1fe816d548c52f313a5e61))
* don't defer the turn-complete note on an abnormal thinking-only stop ([df4eaa8](https://github.com/kaiitunnz/waypoint/commit/df4eaa8ce16c85b3eb73b729709fad10066c14ae))
* emit one claude_tty turn-complete note per message ([8f5111f](https://github.com/kaiitunnz/waypoint/commit/8f5111faf83a2e15ea90c877d24283fb235d8955))
* encode claude_tty transcript path like the Claude CLI ([0b3aa67](https://github.com/kaiitunnz/waypoint/commit/0b3aa67b6caa9ff8607d29b4e36f0c3e1062290b))
* harden terminal-page popover anchoring ([91f18c4](https://github.com/kaiitunnz/waypoint/commit/91f18c4b0a224cd7bf87103f18b442a155b4990c))
* keep terminal-page popovers out of the clipped pane ([d4d56d1](https://github.com/kaiitunnz/waypoint/commit/d4d56d1a24e70fc93614149be74ecab2f622afca))
* make claude_tty dialog detection robust to live pane reality ([ed4f4c7](https://github.com/kaiitunnz/waypoint/commit/ed4f4c7156039bac077340e54fae815027722567))
* match the bottom-most claude_tty dialog block ([eaf00d9](https://github.com/kaiitunnz/waypoint/commit/eaf00d913a94695e1161b9f92852ae37c12c302e))
* prevent duplicate claude_tty approval emit after response ([4ba64dd](https://github.com/kaiitunnz/waypoint/commit/4ba64dda6d02a2f0d45ebbc09d46492a11fcdca5))
* resolve a claude_tty session to idle after a declined tool ([5fa72a5](https://github.com/kaiitunnz/waypoint/commit/5fa72a57851113557797f876ee0470da1d020bc6))
* resume a claude_tty restart only when the thread has a conversation ([b1b056d](https://github.com/kaiitunnz/waypoint/commit/b1b056dd8f643d9e0f9044665f1e117a6af8eb26))
* slim the terminal composer meta row ([8f560c7](https://github.com/kaiitunnz/waypoint/commit/8f560c71423c2c0ed81db405ae983cb2a0ebb12a))
* surface /compact and other Claude built-ins for claude_code ([602bc27](https://github.com/kaiitunnz/waypoint/commit/602bc27a5c43bda13aa67cbcf0f0e54db1cff744))
* surface runnable groups in recursive help ([8fecb9b](https://github.com/kaiitunnz/waypoint/commit/8fecb9b879d7caac93f2f2c0c4f202334f2aedc6))
* thread backend catalog through frontend capability checks ([54f66d6](https://github.com/kaiitunnz/waypoint/commit/54f66d6c639c3cea075a7f459a34ff961757de47))
* treat 'accept' as a tool approval and clear pending on interrupt ([d044904](https://github.com/kaiitunnz/waypoint/commit/d04490430c47a647c576e2ff490c37e4e7a875bf))
