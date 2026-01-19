# Zim Import
Created Friday 12 December 2025
---
## Purpose
Allow the user to point to a folder of zim wiki files and convert them into Zimx file and structure.

## Functionality
i want to add a "File / Import / Zim wiki' feature to mynav menu.  When selected this should prompt a user for a folder or file on their os to select.

The file selector should allow a folder selection, or any *.txt files

a zim file is a collection of .txt files that are linked together in the file with links like:

This is what a zim wiki page looks like.

This is the `Home.txt` file

```
Content-Type: text/x-zim-wiki
Wiki-Format: zim 0.6
Creation-Date: 2025-12-06T05:06:36-06:00

====== FormattingTest ======
Created Tuesday 25 November 2025
--------------------

======  Heading One ======
=====  Heading Two =====
====  Heading Three ====
===  Heading Four ===
==  Heading Five ==

[ ] this is a checkbox @withtag
[*] this is done
[>] this is deferred
[x] this is done
[<] this is waiting

[[./code_1.106.3-1764110892_amd64.deb]]

This is a https://www.google.com
This is also a [[https://www.google.com|gogle link.]]

this is a  [[Wiki|Page Link]]  page link.

this is **bold**.

this is //**bold and italic**//.

this is just //italic//

this is ~~strikethrough~~.

~~this is a multi line strikethrough none of this applies.~~

this is a ''single fixed width'' thing.
```

It links to the `Wiki.txt` file in the same directory.

The folder structure looks like this:

Notebook Folder/
    Home.txt
    Home/attachment.png (contains any of `Home` page attachments)
    Wiki.txt
    Wiki/document.pdf (attachments)

This structure is all relative links to the notebooks home folder.