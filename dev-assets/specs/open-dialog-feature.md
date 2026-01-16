Open Dialog Feature.
--------------------
Application currently loads the last loaded vault.

Updates
--------
maintain a list of previously opened vaults in .stillpoint_config.json

when loading the app, review this file, if a 'default vault' is selected just open that.  If not, present the open vault dialogue.

Open vault dialoge:
I want to add a hot key of ctrl-o which will tie to the existing "Open Vault" option.
The dialog should show a list of previously opened vaults from the local config.
If none show the current vault in the list.

there should be a option to add/remove a vault.
if the vualt is double clicked open the vault (multiple instances is okay).

the vault show list with it's name, then under it in a smaller font the full path the folder.

The add new dialogue should ask for;
Vault Name; <text>
Vault Folder: <folder selection widget>
refresh the list

the dialog show also have a dropdown to select the 'default vault' as a dropdown of current vaules.  If this is set or changed write that to the local config json.

upon startup if the open dailoge is to be shown do not make the main window visible yet. Also put my application icon somewhere on the open vault dialogue.

when i choose to open another vault it should launch in it's own window (second instance of the app).