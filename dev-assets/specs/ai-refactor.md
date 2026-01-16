## ai refactor.

I want my AI chats to have a context and RAG support into chromadb

this context is either

a) global level (generic)

b) page level... RAGs the page content

c) collection... this starts as a page chat but in my chat i can select '@`<space> ' and a popup panel just above the ai chat text gives me a list of pages in the vault that i cna filter by typing more chars, hitting enter adds it to the chat context (and rag).`

typing '#<space' does the same thing but RAGs the whole page tree (minus attachments)

typing '!`<space>' pops a similar list but only of the attachments of pages (still with type ahead search by page).`

i want a single chromadb store per vault, and it stores in the .stillpoint folder.

chromadb should be indexed by page id to support delettion.

the AI panel should have a makrer to indicate it's context and allow a popup to show the context (3 pages, 2 files, etc))
