# Changelog

## [0.11.0](https://github.com/kaiitunnz/waypoint/compare/v0.10.1...v0.11.0) (2026-07-18)


### Features

* add opt-in Telegram notification center ([#322](https://github.com/kaiitunnz/waypoint/issues/322)) ([5f73046](https://github.com/kaiitunnz/waypoint/commit/5f7304622a7189f5eac4c74ad4cd1c187a6af508))
* per-signal notification config and active-session presence suppression ([#324](https://github.com/kaiitunnz/waypoint/issues/324)) ([875c9a3](https://github.com/kaiitunnz/waypoint/commit/875c9a3586f482dcbc86738fa45a3873daf1f694))

## [0.10.1](https://github.com/kaiitunnz/waypoint/compare/v0.10.0...v0.10.1) (2026-07-18)


### Features

* collapsible desktop board sidebar and off-app-bar view switch ([#321](https://github.com/kaiitunnz/waypoint/issues/321)) ([7172a29](https://github.com/kaiitunnz/waypoint/commit/7172a29f0b192b95de331bdb3a1f3678d87ce3b4))
* project-scope the Waypoint Manager backend for multiple managers ([#317](https://github.com/kaiitunnz/waypoint/issues/317)) ([06a9686](https://github.com/kaiitunnz/waypoint/commit/06a9686488188e03312f582508f96413fe642976))
* rework the board page into a mobile-first board workspace with manager mode ([#319](https://github.com/kaiitunnz/waypoint/issues/319)) ([6736e1d](https://github.com/kaiitunnz/waypoint/commit/6736e1ddc83a70e0ccc255fec5a4ac989501d91f))


### Bug Fixes

* order telemetry activity heatmap rows chronologically ([#320](https://github.com/kaiitunnz/waypoint/issues/320)) ([7f79e04](https://github.com/kaiitunnz/waypoint/commit/7f79e04fa83524bbab1f01fff7107d94cb68e8db))


### Miscellaneous Chores

* release 0.10.1 ([47db877](https://github.com/kaiitunnz/waypoint/commit/47db877182a765c217bfbdc706b147fbecf27961))

## [0.10.0](https://github.com/kaiitunnz/waypoint/compare/v0.9.3...v0.10.0) (2026-07-17)


### Features

* autonomous Waypoint Manager MVP — wake, state machine, skill ([#304](https://github.com/kaiitunnz/waypoint/issues/304)) ([6b6ee1b](https://github.com/kaiitunnz/waypoint/commit/6b6ee1b29f2646f5e08d3b7153ef1602c238ab98))

## [0.9.3](https://github.com/kaiitunnz/waypoint/compare/v0.9.2...v0.9.3) (2026-07-17)


### Features

* add a resizable desktop context-usage popover ([#311](https://github.com/kaiitunnz/waypoint/issues/311)) ([850a008](https://github.com/kaiitunnz/waypoint/commit/850a008426c34c9f414e0b6f32ee3ed89657aed1))
* break down the Database metric in telemetry instance health ([#315](https://github.com/kaiitunnz/waypoint/issues/315)) ([1ed879f](https://github.com/kaiitunnz/waypoint/commit/1ed879f4b00a2deb3294d3e4ff02880519ff91d4))


### Bug Fixes

* eliminate empty-insights refresh flash on the telemetry page ([#313](https://github.com/kaiitunnz/waypoint/issues/313)) ([2501038](https://github.com/kaiitunnz/waypoint/commit/250103808d6375d87f5e7a828c8bfa7f7c28cd8f))
* render the workspace dock as a full-screen sheet when it overflows the viewport ([#314](https://github.com/kaiitunnz/waypoint/issues/314)) ([78a0b9e](https://github.com/kaiitunnz/waypoint/commit/78a0b9e94e99b8b919e13f601b2ba34a46643085))


### Miscellaneous Chores

* release 0.9.3 ([d58fa01](https://github.com/kaiitunnz/waypoint/commit/d58fa01f8b94c061abe248c10c919156a3a5dd05))

## [0.9.2](https://github.com/kaiitunnz/waypoint/compare/v0.9.1...v0.9.2) (2026-07-12)


### Features

* add maintenance rebuild-telemetry command to re-run backfill ([#302](https://github.com/kaiitunnz/waypoint/issues/302)) ([14043e9](https://github.com/kaiitunnz/waypoint/commit/14043e95edad78dfeca352547ac7e15fecb91801))
* durable, observable NL-insight regeneration status ([#303](https://github.com/kaiitunnz/waypoint/issues/303)) ([44788a4](https://github.com/kaiitunnz/waypoint/commit/44788a4761655e7a12d9e3e002bab967f5165b5f))


### Bug Fixes

* add instance health and capacity telemetry ([#301](https://github.com/kaiitunnz/waypoint/issues/301)) ([b236e22](https://github.com/kaiitunnz/waypoint/commit/b236e2292513a5258419c08563d5de75c030be32))
* isolate /btw side-questions from built-in and MCP tools ([#298](https://github.com/kaiitunnz/waypoint/issues/298)) ([b661065](https://github.com/kaiitunnz/waypoint/commit/b661065c9a9ca361c4227719a69a10f1b15e4e0c))
* keep light terminal white slot light for ANSI themes ([#300](https://github.com/kaiitunnz/waypoint/issues/300)) ([fdd600c](https://github.com/kaiitunnz/waypoint/commit/fdd600cb11b7192dcb052fea54104ba3cab19931))


### Miscellaneous Chores

* release 0.9.2 ([7472cd3](https://github.com/kaiitunnz/waypoint/commit/7472cd3fc017b1562e8bb821b5a8d8916ad8c3fa))

## [0.9.1](https://github.com/kaiitunnz/waypoint/compare/v0.9.0...v0.9.1) (2026-07-12)


### Features

* make the usage telemetry dashboard opt-in ([#297](https://github.com/kaiitunnz/waypoint/issues/297)) ([eea18ba](https://github.com/kaiitunnz/waypoint/commit/eea18ba5b9a482c2bad7b7adb3842686334af2cc))


### Bug Fixes

* harden the AI usage telemetry dashboard against review findings ([#292](https://github.com/kaiitunnz/waypoint/issues/292)) ([cf5c37a](https://github.com/kaiitunnz/waypoint/commit/cf5c37a0ff80a08a0b2db5bc5b8f7a90ec70f3ef))
* resolve deferred telemetry review items (dead rollups, loop offload, edges) ([#294](https://github.com/kaiitunnz/waypoint/issues/294)) ([1757d9e](https://github.com/kaiitunnz/waypoint/commit/1757d9eaa9596620bdc3a023a9e5f9b46af72677))


### Performance Improvements

* speed up telemetry rollup recompute and dashboard aggregation ([#296](https://github.com/kaiitunnz/waypoint/issues/296)) ([1ac1ffa](https://github.com/kaiitunnz/waypoint/commit/1ac1ffaa250b81e8ac483c9ad81375c6147d9948))


### Miscellaneous Chores

* release 0.9.1 ([7a1c0d2](https://github.com/kaiitunnz/waypoint/commit/7a1c0d2d912154f7f306bd3bd57a329947430bee))

## [0.9.0](https://github.com/kaiitunnz/waypoint/compare/v0.8.2...v0.9.0) (2026-07-12)


### Features

* add opt-in natural-language telemetry insights ([#289](https://github.com/kaiitunnz/waypoint/issues/289)) ([bbe8e9e](https://github.com/kaiitunnz/waypoint/commit/bbe8e9e22b42053474d5cd4b2f072af61a8200a8))
* add the AI usage telemetry dashboard ([#288](https://github.com/kaiitunnz/waypoint/issues/288)) ([4c00f16](https://github.com/kaiitunnz/waypoint/commit/4c00f16369b4ce3c62b7b113b516453210028c91))

## [0.8.2](https://github.com/kaiitunnz/waypoint/compare/v0.8.1...v0.8.2) (2026-07-11)


### Bug Fixes

* preserve Claude context-window model provenance across resume ([#285](https://github.com/kaiitunnz/waypoint/issues/285)) ([5c14f22](https://github.com/kaiitunnz/waypoint/commit/5c14f228185b3e9eeeb406acf52a1046b4d9d1ad))
* suppress phantom "No response requested." turn after claude_tty resume ([#287](https://github.com/kaiitunnz/waypoint/issues/287)) ([20f4652](https://github.com/kaiitunnz/waypoint/commit/20f4652ec193cd0697c629e10406b9c8235f85e9))
* tolerate unknown Codex thread item types on session reattach ([#283](https://github.com/kaiitunnz/waypoint/issues/283)) ([f081580](https://github.com/kaiitunnz/waypoint/commit/f081580219b5a4bb2929bdc4d2089211b9434d6d))

## [0.8.1](https://github.com/kaiitunnz/waypoint/compare/v0.8.0...v0.8.1) (2026-07-11)


### Features

* sync the terminal surface to the Claude TUI theme ([#281](https://github.com/kaiitunnz/waypoint/issues/281)) ([710e354](https://github.com/kaiitunnz/waypoint/commit/710e35426288dfab61f7eccf238574f203eb3b1d))


### Miscellaneous Chores

* release 0.8.1 ([745570f](https://github.com/kaiitunnz/waypoint/commit/745570f84669abd66f6877c7982682ace27ac446))

## [0.8.0](https://github.com/kaiitunnz/waypoint/compare/v0.7.4...v0.8.0) (2026-07-10)


### Features

* switch a managed session's transport from Session settings ([#279](https://github.com/kaiitunnz/waypoint/issues/279)) ([9ff385b](https://github.com/kaiitunnz/waypoint/commit/9ff385bd7806080402402f0e03c2ca770b832f47))

## [0.7.4](https://github.com/kaiitunnz/waypoint/compare/v0.7.3...v0.7.4) (2026-07-10)


### Features

* add a full session settings editor ([#275](https://github.com/kaiitunnz/waypoint/issues/275)) ([bab940b](https://github.com/kaiitunnz/waypoint/commit/bab940b0eecb30f8cdc8a0c38e9eb8d1380d0f4d))
* recursively merge populated transcript stores on setup-transcripts ([#273](https://github.com/kaiitunnz/waypoint/issues/273)) ([09e85ae](https://github.com/kaiitunnz/waypoint/commit/09e85aee3de790bc61fb472bc2ac4f9ab8f9f012))
* separate session context window from cumulative token telemetry ([#277](https://github.com/kaiitunnz/waypoint/issues/277)) ([6e90c50](https://github.com/kaiitunnz/waypoint/commit/6e90c508b3c82e8463640add723082e2d16a1eef))
* tail remote claude_tty transcripts over the SSH seam ([#278](https://github.com/kaiitunnz/waypoint/issues/278)) ([6145677](https://github.com/kaiitunnz/waypoint/commit/6145677265e55ac668f94f999cb4b72eff8ea8bb))


### Bug Fixes

* order pin before settings on session cards and match hover style ([#276](https://github.com/kaiitunnz/waypoint/issues/276)) ([20d2397](https://github.com/kaiitunnz/waypoint/commit/20d2397d530a6fe9a119ae7de8957acd0c9fb2e4))


### Miscellaneous Chores

* release 0.7.4 ([c98350c](https://github.com/kaiitunnz/waypoint/commit/c98350caaaa3cfa6e65a2b09dbe925663cb036e5))

## [0.7.3](https://github.com/kaiitunnz/waypoint/compare/v0.7.2...v0.7.3) (2026-07-10)


### Features

* make assistant lifecycle profile-aware ([#270](https://github.com/kaiitunnz/waypoint/issues/270)) ([7982506](https://github.com/kaiitunnz/waypoint/commit/798250611e4d49231c50b7bb95f236998ca9d600))


### Bug Fixes

* restart unpersisted Codex threads on profile switch ([#272](https://github.com/kaiitunnz/waypoint/issues/272)) ([ae72604](https://github.com/kaiitunnz/waypoint/commit/ae7260494922a6a8acb6f11e2925f48497f195c0))


### Miscellaneous Chores

* release 0.7.3 ([7ec4be1](https://github.com/kaiitunnz/waypoint/commit/7ec4be1cf911972c93ed5b8475f23531460c2de1))

## [0.7.2](https://github.com/kaiitunnz/waypoint/compare/v0.7.1...v0.7.2) (2026-07-09)


### Features

* honor local_bin for local codex launches ([#268](https://github.com/kaiitunnz/waypoint/issues/268)) ([c933f00](https://github.com/kaiitunnz/waypoint/commit/c933f00ddd8121fede1452098f69b1d9b443766d))


### Miscellaneous Chores

* release 0.7.2 ([53226a2](https://github.com/kaiitunnz/waypoint/commit/53226a265c0e172dc4de124d520a94e087ba5772))

## [0.7.1](https://github.com/kaiitunnz/waypoint/compare/v0.7.0...v0.7.1) (2026-07-09)


### Features

* add compact session read output ([#267](https://github.com/kaiitunnz/waypoint/issues/267)) ([1d31e3a](https://github.com/kaiitunnz/waypoint/commit/1d31e3aee49bfd74fc62d1485ae225371209bdf5))
* add waypoint research skill ([#266](https://github.com/kaiitunnz/waypoint/issues/266)) ([42b0523](https://github.com/kaiitunnz/waypoint/commit/42b0523ec0e3b94fe42a07807090095f071ec4a8))
* switch account profiles on tmux-wrapped sessions ([#264](https://github.com/kaiitunnz/waypoint/issues/264)) ([2cfd9b3](https://github.com/kaiitunnz/waypoint/commit/2cfd9b34fe6faf4fa57409026ee6a5052e3b0eac))


### Miscellaneous Chores

* release 0.7.1 ([8e3362c](https://github.com/kaiitunnz/waypoint/commit/8e3362cbd6a4d417e2b1115434df740ab0426a3c))

## [0.7.0](https://github.com/kaiitunnz/waypoint/compare/v0.6.2...v0.7.0) (2026-07-09)


### Features

* account-profile selection and display in the web UI ([#242](https://github.com/kaiitunnz/waypoint/issues/242)) ([55e3645](https://github.com/kaiitunnz/waypoint/commit/55e3645185ce0523cc4695103feeaf270b486d1a))
* add account/config-dir profiles (config, capabilities, metadata) ([#231](https://github.com/kaiitunnz/waypoint/issues/231)) ([28a19b6](https://github.com/kaiitunnz/waypoint/commit/28a19b66297cafa6fb21e16ae2aacc71a5be8321))
* add account/config-profile switching to the CLI ([#238](https://github.com/kaiitunnz/waypoint/issues/238)) ([f891e34](https://github.com/kaiitunnz/waypoint/commit/f891e34e698348def63f4b9ec06fd38676958c47))
* add accounts probe/doctor/setup-transcripts CLI and endpoints ([#251](https://github.com/kaiitunnz/waypoint/issues/251)) ([35b5e6e](https://github.com/kaiitunnz/waypoint/commit/35b5e6e13470b5651e4436cc8b3a3ccd60af0c7d))
* add native transcript availability for profile switching ([#236](https://github.com/kaiitunnz/waypoint/issues/236)) ([49f7117](https://github.com/kaiitunnz/waypoint/commit/49f711744c9efb59e2cf1c9b7048295c39360f11))
* elevate account profile to session context in the web UI ([#260](https://github.com/kaiitunnz/waypoint/issues/260)) ([dd73734](https://github.com/kaiitunnz/waypoint/commit/dd737346dc9c48e90ea5429ebe49b8fdf8ca480d))
* extend account-profile switching to SSH remote launch targets ([#259](https://github.com/kaiitunnz/waypoint/issues/259)) ([2ba3ff5](https://github.com/kaiitunnz/waypoint/commit/2ba3ff501e0ec0b6319c8df2e630181542976534))
* live account/launch-settings switch via restart-and-resume ([#237](https://github.com/kaiitunnz/waypoint/issues/237)) ([2529995](https://github.com/kaiitunnz/waypoint/commit/2529995ff59f6884cba00cf431180de9119cffe6))
* persist and apply account-profile selection at launch ([#234](https://github.com/kaiitunnz/waypoint/issues/234)) ([15037d1](https://github.com/kaiitunnz/waypoint/commit/15037d11364f66d5aa2e4e934077758378a4c50a))
* persist verified-account provenance on sessions ([#263](https://github.com/kaiitunnz/waypoint/issues/263)) ([e8ef802](https://github.com/kaiitunnz/waypoint/commit/e8ef802ee29570ff89adb7e37f7ba84e1938ce3d))
* reject account profiles whose config dir isn't set up ([#250](https://github.com/kaiitunnz/waypoint/issues/250)) ([947190c](https://github.com/kaiitunnz/waypoint/commit/947190c5180b14ac7416b54fc77f9ddf4a56cc9d))
* scope discovery (models, threads, import, delete) to the account profile ([#261](https://github.com/kaiitunnz/waypoint/issues/261)) ([735d341](https://github.com/kaiitunnz/waypoint/commit/735d3416c67588413f5d32ba452122fbdfd03be9))
* switch a running session's account profile from the composer ([#243](https://github.com/kaiitunnz/waypoint/issues/243)) ([8630325](https://github.com/kaiitunnz/waypoint/commit/8630325ada7d54bea561555e8e0e78795b9af779))


### Bug Fixes

* add transcript response submit shortcuts ([#240](https://github.com/kaiitunnz/waypoint/issues/240)) ([73913ff](https://github.com/kaiitunnz/waypoint/commit/73913ff658389ba160a261cb006ae136cd600679))
* probe rate limits with the session's account env ([#235](https://github.com/kaiitunnz/waypoint/issues/235)) ([6e5492d](https://github.com/kaiitunnz/waypoint/commit/6e5492d2d42eeeb1c8202cef8292e2ae6c12f6ca))
* relaunch the pane when switching launch settings on claude_tty ([#239](https://github.com/kaiitunnz/waypoint/issues/239)) ([4d3ecda](https://github.com/kaiitunnz/waypoint/commit/4d3ecdafdab2eb65337b0b5bdb3642026c279d3a))
* resolve codex per-session ops against the profile CODEX_HOME ([#247](https://github.com/kaiitunnz/waypoint/issues/247)) ([5da8484](https://github.com/kaiitunnz/waypoint/commit/5da84846ceb8699fd4523cfa3c528430e1c9319e))
* resume the same thread across an account switch on pane transports ([#241](https://github.com/kaiitunnz/waypoint/issues/241)) ([2861ebf](https://github.com/kaiitunnz/waypoint/commit/2861ebfc83edea3134c4b03fb6b455eba8dce5b4))
* scope claude_code per-session ops to the profile config dir ([#248](https://github.com/kaiitunnz/waypoint/issues/248)) ([5d995e5](https://github.com/kaiitunnz/waypoint/commit/5d995e54b961f8a626fbab3d0bd9e313284e5db3))
* scope claude_tty plan-file detection to the profile config dir ([#249](https://github.com/kaiitunnz/waypoint/issues/249)) ([92d7f5f](https://github.com/kaiitunnz/waypoint/commit/92d7f5f1f83025527373be3b0acda1179abbbe06))
* tail the profile's config dir for claude_tty sessions ([#246](https://github.com/kaiitunnz/waypoint/issues/246)) ([45b5cd3](https://github.com/kaiitunnz/waypoint/commit/45b5cd37c2973531c7624533bbf8abf418a8ce6c))
* top-align session-context field row so controls share an edge ([#262](https://github.com/kaiitunnz/waypoint/issues/262)) ([4f4f708](https://github.com/kaiitunnz/waypoint/commit/4f4f7086b9fe6b7a0212307b487383299d4e4521))

## [0.6.2](https://github.com/kaiitunnz/waypoint/compare/v0.6.1...v0.6.2) (2026-07-06)


### Bug Fixes

* confirm preset deletion and stop preset save from launching a session ([#228](https://github.com/kaiitunnz/waypoint/issues/228)) ([6e61c43](https://github.com/kaiitunnz/waypoint/commit/6e61c438b079edcebe3f5130f791fa16bbec88dc))

## [0.6.1](https://github.com/kaiitunnz/waypoint/compare/v0.6.0...v0.6.1) (2026-07-06)


### Bug Fixes

* support session path copying ([#226](https://github.com/kaiitunnz/waypoint/issues/226)) ([d9c4017](https://github.com/kaiitunnz/waypoint/commit/d9c4017eeb19084e6a691968b31c46c78c49d452))

## [0.6.0](https://github.com/kaiitunnz/waypoint/compare/v0.5.1...v0.6.0) (2026-07-05)


### Features

* add session creation presets ([#224](https://github.com/kaiitunnz/waypoint/issues/224)) ([6886eff](https://github.com/kaiitunnz/waypoint/commit/6886eff24f97ca2a7f96facb2219920790175091))

## [0.5.1](https://github.com/kaiitunnz/waypoint/compare/v0.5.0...v0.5.1) (2026-07-05)


### Bug Fixes

* add launch environment variables ([#222](https://github.com/kaiitunnz/waypoint/issues/222)) ([5fb7677](https://github.com/kaiitunnz/waypoint/commit/5fb7677e825af019860c36a4efea374388dc2089))

## [0.5.0](https://github.com/kaiitunnz/waypoint/compare/v0.4.1...v0.5.0) (2026-07-04)


### Features

* add a user inbox for lead-initiated human checkpoints ([#218](https://github.com/kaiitunnz/waypoint/issues/218)) ([7cc9453](https://github.com/kaiitunnz/waypoint/commit/7cc945351fda4d081f64f366d1c801bd6a59df5a))
* add batch deletion to the inbox (select + delete-resolved) ([#220](https://github.com/kaiitunnz/waypoint/issues/220)) ([0521e8b](https://github.com/kaiitunnz/waypoint/commit/0521e8bce6b1e19696a2c99d9079e77b264e1c70))
* add board ready dep-satisfaction query ([#216](https://github.com/kaiitunnz/waypoint/issues/216)) ([455aee5](https://github.com/kaiitunnz/waypoint/commit/455aee51a4cb0568e3d2c68901ba536a7b950b70))
* add waypoint-crew skill for autonomous engineering orgs ([#210](https://github.com/kaiitunnz/waypoint/issues/210)) ([1780130](https://github.com/kaiitunnz/waypoint/commit/1780130a36c02f83bad2c4444066e0f1441e6b6d))
* patch board cell metadata with set-meta --merge ([#212](https://github.com/kaiitunnz/waypoint/issues/212)) ([132f93e](https://github.com/kaiitunnz/waypoint/commit/132f93e4795d996040f833a658f9e6fd5717288b))
* pin and manage session attachments from the CLI ([#213](https://github.com/kaiitunnz/waypoint/issues/213)) ([a49812a](https://github.com/kaiitunnz/waypoint/commit/a49812a94d086aaac473ae2f242b34e03f1f1902))
* recursive spawn-tree and idle filtering for sessions ([#215](https://github.com/kaiitunnz/waypoint/issues/215)) ([5e38c85](https://github.com/kaiitunnz/waypoint/commit/5e38c85cd4dfdd0d6c2c1241ac20d80cb061b488))
* scroll buttons and key-bar for desktop and the emulated pane ([#217](https://github.com/kaiitunnz/waypoint/issues/217)) ([4ccffc1](https://github.com/kaiitunnz/waypoint/commit/4ccffc163895b2af8db3792ed5c51ed0492a3e97))
* session tags and selective reap ([#214](https://github.com/kaiitunnz/waypoint/issues/214)) ([073d481](https://github.com/kaiitunnz/waypoint/commit/073d4810b80c68167e152bf93cbb27d43b1599b1))

## [0.4.1](https://github.com/kaiitunnz/waypoint/compare/v0.4.0...v0.4.1) (2026-07-02)


### Bug Fixes

* reject a nonexistent working directory at session launch ([#206](https://github.com/kaiitunnz/waypoint/issues/206)) ([5a05b10](https://github.com/kaiitunnz/waypoint/commit/5a05b106de666da883e2172158e7caa5688eae80))
* replay thread history into imported sessions ([#208](https://github.com/kaiitunnz/waypoint/issues/208)) ([c2fcc67](https://github.com/kaiitunnz/waypoint/commit/c2fcc674e238558c52e8725dd2b158d67d58a95e))
* unify row actions and dock expand behavior ([#209](https://github.com/kaiitunnz/waypoint/issues/209)) ([d69ab66](https://github.com/kaiitunnz/waypoint/commit/d69ab66219f77d6a47c6ea96cb926e07fe67e17c))

## [0.4.0](https://github.com/kaiitunnz/waypoint/compare/v0.3.0...v0.4.0) (2026-07-02)


### Features

* restyle the design system — flat instruments, liquid-glass chrome ([#203](https://github.com/kaiitunnz/waypoint/issues/203)) ([686cd13](https://github.com/kaiitunnz/waypoint/commit/686cd13bf6002c07937cfb4469a7618ec53d0d6d))


### Bug Fixes

* keep the task dock visible for out-of-window todos ([#205](https://github.com/kaiitunnz/waypoint/issues/205)) ([53ca3b8](https://github.com/kaiitunnz/waypoint/commit/53ca3b82801860c2667fe32ccef67e2571ad5bf1))

## [0.3.0](https://github.com/kaiitunnz/waypoint/compare/v0.2.0...v0.3.0) (2026-07-01)


### Features

* add /btw side-questions to the Claude agent ([#181](https://github.com/kaiitunnz/waypoint/issues/181)) ([1d266aa](https://github.com/kaiitunnz/waypoint/commit/1d266aaee600f57ed50e625c4ac85e7df8204a35))
* add `update --check` to report available updates without applying ([#185](https://github.com/kaiitunnz/waypoint/issues/185)) ([a18c964](https://github.com/kaiitunnz/waypoint/commit/a18c964eace0c0cd0f85b3d7fc49b9c32a30f2d0))
* add event coalescing to CLI sessions events and output ([#183](https://github.com/kaiitunnz/waypoint/issues/183)) ([6442852](https://github.com/kaiitunnz/waypoint/commit/6442852d395d0983fbd58457e297ac5ee3fe6ff6))
* add password SSH auth for remote coding sessions ([#189](https://github.com/kaiitunnz/waypoint/issues/189)) ([0f0dea9](https://github.com/kaiitunnz/waypoint/commit/0f0dea949c75082089a4b4b54cce3938d2d517a6))
* add scheduled messages for sessions ([#192](https://github.com/kaiitunnz/waypoint/issues/192)) ([091ef40](https://github.com/kaiitunnz/waypoint/commit/091ef4068529e0704357d6f03c50cf944c0c7ba1))
* delete resumable threads from the resume list ([#186](https://github.com/kaiitunnz/waypoint/issues/186)) ([c8ec6b7](https://github.com/kaiitunnz/waypoint/commit/c8ec6b7edce4e8e5643b9b3ed6c08c81be86a4c6))
* make the Claude model & effort catalogue CLI-version aware ([#191](https://github.com/kaiitunnz/waypoint/issues/191)) ([001a6e6](https://github.com/kaiitunnz/waypoint/commit/001a6e66ba4b7a66b5b86fb3cfe65c58e4d74ff7))
* polish the session list layout and add a top pager ([#194](https://github.com/kaiitunnz/waypoint/issues/194)) ([bc8840f](https://github.com/kaiitunnz/waypoint/commit/bc8840f51c8046d1473a1ba84fb9fb4e92177111))
* restyle session/schedule actions as chips and align scheduled rows ([#187](https://github.com/kaiitunnz/waypoint/issues/187)) ([cf337eb](https://github.com/kaiitunnz/waypoint/commit/cf337ebd74074e33b801a4b6c191e0ea9334fd19))
* support deleting resumable OpenCode threads ([#190](https://github.com/kaiitunnz/waypoint/issues/190)) ([715c741](https://github.com/kaiitunnz/waypoint/commit/715c741a3dd5f42f479a8ed8ff37698c88aa53f4))


### Bug Fixes

* keep wide tasks from overflowing the task dock past the viewport ([#188](https://github.com/kaiitunnz/waypoint/issues/188)) ([c6ce413](https://github.com/kaiitunnz/waypoint/commit/c6ce41391172252ea45e4ffb054510fb8bd40269))

## [0.2.0](https://github.com/kaiitunnz/waypoint/compare/v0.1.3...v0.2.0) (2026-06-26)


### Features

* add a branch chip, paginated tree, and file finder to the explorer ([#178](https://github.com/kaiitunnz/waypoint/issues/178)) ([da3681d](https://github.com/kaiitunnz/waypoint/commit/da3681d3620fd3fdef029e8169e4a2a1e2934e1f))
* add a copy button and syntax highlighting to transcript code blocks ([#175](https://github.com/kaiitunnz/waypoint/issues/175)) ([c8af340](https://github.com/kaiitunnz/waypoint/commit/c8af3402bac803b1229321d5a7502837ece58871))
* add a waypointd out-of-band remote-control console ([#179](https://github.com/kaiitunnz/waypoint/issues/179)) ([5a309d6](https://github.com/kaiitunnz/waypoint/commit/5a309d65260c1412218cca1fdb6278cdd3e96d35))
* add git status and a full-file diff viewer to the file explorer ([#177](https://github.com/kaiitunnz/waypoint/issues/177)) ([6b85d90](https://github.com/kaiitunnz/waypoint/commit/6b85d90e2f9bc22c63e0e343a8e84541d0b20306))


### Bug Fixes

* rework the control console for robust, concurrent ops ([#180](https://github.com/kaiitunnz/waypoint/issues/180)) ([c37e5d2](https://github.com/kaiitunnz/waypoint/commit/c37e5d22508f5dfd540efe116fb8ceb78af66672))

## [0.1.3](https://github.com/kaiitunnz/waypoint/compare/v0.1.2...v0.1.3) (2026-06-25)


### Bug Fixes

* reinstall waypointctl on update so the version isn't masked by uv's cache ([#173](https://github.com/kaiitunnz/waypoint/issues/173)) ([6d4b811](https://github.com/kaiitunnz/waypoint/commit/6d4b81164ba7755a9aa8b0e1dfd68036f9c42c35))

## [0.1.2](https://github.com/kaiitunnz/waypoint/compare/v0.1.1...v0.1.2) (2026-06-25)


### Bug Fixes

* add a clean uninstall path (waypointctl uninstall + uninstall.sh) ([#171](https://github.com/kaiitunnz/waypoint/issues/171)) ([2f7b851](https://github.com/kaiitunnz/waypoint/commit/2f7b851917e8c21307a62cca1a3865cc6073b0e3))
* don't start or restart the stack during install and update ([#169](https://github.com/kaiitunnz/waypoint/issues/169)) ([507dd98](https://github.com/kaiitunnz/waypoint/commit/507dd98deea436c08e841a71c5949c0f44f19bb6))
* persist WAYPOINT_HOME only to the primary shell profile ([#170](https://github.com/kaiitunnz/waypoint/issues/170)) ([6cc2e87](https://github.com/kaiitunnz/waypoint/commit/6cc2e87f2bfb0617e613ec62545c8aec7b5d7a4e))

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
