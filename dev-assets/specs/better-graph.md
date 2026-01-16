# StillPoint Link Navigator Graph Upgrades (Obsidian-style usefulness)
---

## Goals

Create a new graph view on the link navigator panel that feels more like Obsidian’s graph: useful for exploration, not just visualization.

Key outcomes:
- Reduce visual clutter (labels, collisions)
- Improve meaning density (node size by degree, better badges)
- Add powerful filtering (depth, tags, query)
- Add physics layout (force-directed) while preserving stable layouts
- Strengthen keyboard-centric exploration loop (pivot, expand, pin)

Non-goals:
- Replace tree navigation
- Add heavy ontology/schema modeling
- Build a global “whole vault” graph as the primary workflow (local graph remains primary)

---

## Current State (baseline)

- QGraphicsScene/QGraphicsView with:
  - current page node
  - forward links and backlinks (color-coded)
- Layout modes:
  - Concentric / layered
  - Treemap
- Navigation:
  - keyboard and mouse
- Visuals:
  - current page (blue)
  - links-to-here (green)
  - links-from-here (orange)
  - “stacked dots” indicate hidden/stacked items

---

## High-Level UX Requirements

### Primary workflows
1. **Local exploration**
   - Start at a page
   - See backlinks + forward links
   - Pivot to another node quickly
   - Expand neighborhood / depth
2. **Answering “what’s important?”**
   - Hubs should look bigger
   - Bridge notes should stand out when exploring
3. **Focused views**
   - Filter by tags and query (include/exclude)
   - Constrain to current subtree filter

---

## Feature Set

### 1) Label Policy (reduce clutter)

#### Requirements
- Default: **no labels** on non-selected nodes
- Show labels when:
  - node is hovered (mouse)
  - node is keyboard-focused / selected
  - node is pinned (optional toggle)
- Center node label:
  - must not collide with edges/nodes
  - should be shown as **overlay** (top-left or top-center HUD), OR offset outside the central node

#### Acceptance Criteria
- Concentric layout screenshot equivalent has no overlapping title text on edges
- Hovering any node shows its label within 150ms
- Keyboard selection shows label even without hover

---

### 2) Node Sizing by Link Degree

#### Requirements
- Nodes have radius proportional to degree using a tame scale:
  - `radius = base + k * sqrt(degree)` (or `log1p`)
- Clamp min/max radius (configurable)
- Degree sources:
  - **Local degree**: edges within currently displayed subgraph
  - **Global degree**: links across the entire vault (precomputed)
- Provide a toggle:
  - `Size by: Local | Global`

#### Acceptance Criteria
- A hub note clearly appears larger than a leaf note
- No node exceeds max radius even for extreme hubs
- Node sizes update when filters change (because local degree changes)

---

### 3) Filters (Depth + Direction + Tags + Query)

#### 3.1 Depth filter (local graph radius)
- Control: slider or stepped selector `1 / 2 / 3` (default = 1 or 2)
- Applies from the current center node
- Must rebuild the visible subgraph quickly

#### 3.2 Direction filters
- Toggles:
  - Backlinks (links to center / inward)
  - Forward links (links from center / outward)
- Both on by default

#### 3.3 Tag filters (`@tag`)
- Pages can contain tags formatted as `@tag`
- Controls:
  - Include tags (OR by default; optional AND mode toggle)
  - Exclude tags
- Behavior:
  - If include_tags not empty, node must match include rule
  - If node matches any exclude tag, hide or fade it

#### 3.4 Query filter
- Single-line query input that filters nodes (and implicitly edges)
- Minimum implementation: substring match against:
  - title
  - path/id
  - tags text
- Optional future: mini-grammar
  - `tag:@foo`
  - `-tag:@bar`
  - `path:projects/`
  - `title:"some phrase"`

#### 3.5 Hide vs Fade
- Provide option:
  - `Hide filtered nodes`
  - `Fade filtered nodes` (opacity ~0.10 and ignore mouse events)

#### 3.6 Constrain to Tree/Subtree Filter
- If the main app has an active tree filter, provide a toggle:
  - `Constrain to current tree filter`
- Behavior:
  - Hide/fade nodes outside the allowed subtree scope

#### Acceptance Criteria
- Changing depth redraws graph within 200ms for typical local graphs
- Tag include/exclude visibly updates nodes and edges
- Query filters apply live (debounced ~150ms)
- Hide/fade mode works as configured

---

### 4) Physics-Based Layout (Force Directed)

#### Requirements
- Add a new layout mode:
  - `Physics` (force-directed)
- Must work with QGraphicsScene/QGraphicsView
- Forces:
  - Node-node repulsion
  - Edge springs (Hooke)
  - Mild gravity to keep cluster centered
  - Damping to settle
  - Collision/min distance to reduce overlap
- Simulation control:
  - Runs via QTimer while active/unstable
  - Stops when stable (max velocity under threshold for N frames)
  - Restarts on:
    - filter changes
    - depth changes
    - node pin/unpin changes
    - pivot to new center

#### Drag & Pin
- Dragging a node pins it automatically
- Keyboard toggle pin:
  - `P` pin/unpin selected node
- UI button:
  - `Unpin all`

#### Performance Constraints
- Typical local graphs should feel smooth up to ~200 nodes
- Simulation should stop when stable to prevent idle CPU burn

#### Acceptance Criteria
- Physics layout clusters related nodes visibly (not a symmetric wheel)
- Dragging a node positions it and it stays (pinned)
- Layout settles and simulation halts without user action
- CPU usage drops to near idle after settle

---

### 5) Exploration Controls (Keyboard + Mouse)

#### Keyboard map (required)
- `Arrow keys` / existing nav: move focus among nodes
- `Enter`: open focused node in editor
- `Space`: pivot / recenter local graph on focused node (new center)
- `E`: expand neighborhood (increase depth by 1 up to max, or expand from selected node)
- `P`: pin/unpin node
- `Esc`: clear selection / reset highlight
- Optional:
  - `/` focus query box
  - `F` focus query box

#### Mouse behavior (required)
- Hover: show label tooltip or inline label
- Click: select node
- Double-click: open node
- Drag node: pin and move

#### Acceptance Criteria
- Pivoting is instant and updates the center node + neighborhood
- Keyboard-only usage can pivot/expand/open without needing mouse

---

### 6) Improve “Stacked Dots” into Explicit Meaning

#### Requirements
Replace or augment the dot stacks with one of:
- **Count badge** (preferred): `+N`
- Or ring segments around node:
  - green segment = backlinks count
  - orange segment = forward links count
- Tooltip must explain:
  - what the count means
  - whether they are hidden due to filters/depth

#### Acceptance Criteria
- Users can immediately interpret what the visual indicator means without guessing
- Tooltip provides exact numbers and reason for hidden items

---

## UI Changes

### Controls (top bar)
- Layout selector:
  - Concentric
  - Physics (new)
  - Treemap
- Toggles:
  - Backlinks
  - Forward links
  - Hide vs Fade filtered nodes
  - Constrain to current tree filter
- Depth selector (step slider)
- Size mode:
  - Local degree
  - Global degree
- Tag controls:
  - include tags (chip list)
  - exclude tags (chip list)
- Query input:
  - search/filter box

### HUD / status line
- Show focused node:
  - title
  - path
  - tags
  - backlinks/forward counts

---

## Implementation Notes (PySide6 / QGraphicsScene)

### Suggested class structure
- `GraphViewWidget(QWidget)`
  - owns QGraphicsView, toolbars, controls
  - owns a `GraphController`
- `GraphController`
  - holds model + visible subgraph
  - rebuilds visible nodes/edges when filters change
  - coordinates layout engines
- `GraphNodeItem(QGraphicsEllipseItem)`
  - stores node_id
  - handles hover/selection label behavior
  - supports drag pinning (ItemIsMovable)
- `GraphEdgeItem(QGraphicsLineItem)`
  - stores src_id, dst_id or references to node items
  - updates line endpoints when nodes move
- `ForceLayoutEngine`
  - holds node positions/velocities
  - tick() applies forces and updates item positions
  - detects stability and stops timer

### Rendering hints
- Enable antialiasing
- Prefer `QGraphicsSimpleTextItem` for labels (lighter than rich text)
- Stop simulation timer when stable

---

## Data Requirements

### Tags extraction
- Parse tags from page content:
  - any token matching `@[\w\-]+`
- Maintain `node.tags: set[str]`

### Degree computation
- Precompute global degree for each page (during indexing)
- Compute local degree for the current visible subgraph (on rebuild)

### Graph rebuild triggers
- Page changed (new center)
- Depth changed
- Filters changed (tags/query/direction/subtree)
- Layout mode changed

---

## Acceptance Test Checklist

### Visual clarity
- [ ] Non-selected nodes do not show labels by default
- [ ] Center title does not overlap edges/nodes
- [ ] Hover/focus label behavior works

### Filtering
- [ ] Depth changes update visible neighborhood correctly
- [ ] Tag include/exclude works
- [ ] Query filter works and is debounced
- [ ] Hide/fade works as configured
- [ ] Subtree constraint works

### Physics layout
- [ ] Force layout runs, settles, and stops
- [ ] Drag pins nodes
- [ ] Pin/unpin via keyboard works
- [ ] CPU drops after settle

### Navigation
- [ ] Enter opens focused node
- [ ] Space pivots center node
- [ ] E expands neighborhood
- [ ] Esc clears selection

### Metrics/telemetry (optional)
- [ ] Log visible node count + edge count after rebuild
- [ ] Log physics ticks until settle + avg ms per tick (debug only)

---

## Rollout Plan

Phase 1 (high impact, low risk)
- Label policy (hover/focus only) + center label HUD
- Node sizing by degree (local/global toggle)
- Depth + direction toggles

Phase 2 (Obsidian feel)
- Physics layout + pin/unpin + settle/stop
- Query filter + tag include/exclude

Phase 3 (polish)
- Replace stacked dots with explicit badges/rings
- Search focus shortcuts (/)
- Treemap purpose improvements (optional)

---

## Open Questions (defaults to implement without blocking)

- Include tags mode: OR vs AND (default OR; add optional AND toggle later)
- Max physics node count before auto-fallback (default allow up to 300 local nodes)
- Expand behavior for `E`: increase global depth vs expand selected node only
