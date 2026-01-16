# Obsidian Markdown Stress Test

> This document is designed to exercise **nearly every Markdown feature Obsidian supports**, including Obsidian-specific extensions.

---

## Headings

# H1
## H2
### H3
#### H4
##### H5
###### H6

---

## Emphasis & Text Styling

- *Italic*
- **Bold**
- ***Bold + Italic***
- ~~Strikethrough~~
- ==Highlight==
- `Inline code`

---

## Paragraphs & Line Breaks

This is a normal paragraph.

This is another paragraph separated by a blank line.  
This line ends with two spaces to force a line break.

---

## Lists

### Unordered

- Item A
  - Subitem A1
    - Sub-subitem A1a
- Item B
- Item C

### Ordered

1. First
2. Second
   3. Nested
   4. Nested
5. Third

### Task Lists

- [ ] Incomplete task
- [x] Completed task
- [>] Forwarded
- [-] Cancelled
- [?] Questionable
- [!] Important
- [*] Starred

---

## Links

- External link: [Obsidian](https://obsidian.md)
- Wiki link: [[Internal Note]]
- Wiki link with alias: [[Internal Note|Pretty Name]]
- Heading link: [[Internal Note#Section]]
- Block reference: [[Internal Note^block-id]]

---

## Images & Embeds

![Alt text](https://via.placeholder.com/150)

![[Internal Note]]

---

## Code Blocks

### Inline

Use `git status` to check changes.

![[Pasted image 20251216232514.png]]
### Fenced

```bash
echo "Hello, Obsidian"
```
```