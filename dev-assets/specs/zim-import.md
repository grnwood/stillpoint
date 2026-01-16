# Zim Import

## Purpose
Allow the user to point to a folder of zim wiki files and convert them into Zimx file and structure.

## Zim structure
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

#### Zim wiki's file structure

```
Wiki.txt    
Wiki/attachment.png
    SubPage.txt
    SubPage/attachment.png
```

#### StillPoint's file structure
```
Wiki/
Wiki/Wiki.txt    
Wiki/attachment.png
    SubPage/
    SubPage/SubPage.txt
    SubPage/attachment.png
```

## Functionality

### Folder source and Folder Target
i want to add a "File / Import / Zim wiki' feature to mynav menu.  When selected this should prompt a user for a folder or file on their os to select.

The file selector should allow a folder selection, or any *.txt files

Under that It should also prompt for type ahead seach box (see the jump to dialog) for the target of where to import these files to in the StillPoint wiki structure.

As the import is working there should be a dialogue progress (similar to how the index rebuild dialog).

When the process is complete it should confirm the page count that it moved over.  It should then prompt the user like so:

`Import complete. Moved X files.  Would you like to re-index the vault?`

Confirming this will reindex the vault.


### Translation Rules
After selection, StillPoint should scan the folder for any .txt files and write them into the StillPoint vault on disk.  They should be converted from the Zim wiki format in the following ways:

- Indexing does NOT occurr at this point just file converation and movement.
- Folder structure will translate into the StillPoint folder structure (which is similar but slightly different)
- New file and folder will have the same name as the original filename or folder.
- The content type headers will be removed in the target file.
- All formatting features will be translated into their StillPoint equivalent, there should be a 1:1 feature parity.  If something cannot be matches just write it in the new file as plain text.
- if the source folder has attachments they will be moved into the attachments folder of the new StillPoint page and any relative file links will be translated on the new page.
- any tasks encountered can be translated to StillPoint as either completed tasks or open tasks, thats all that we support.

