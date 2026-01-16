I want to add a hotkey while in the editor.

ctrl-d to insert a date.

when this is pressed i want a calendar widget to pop up near where the cursor point is.  This should also work from the right click menu in the editor 'Insert Date...'

The calendar will be defauled to the current date but is selectable.
below that calendar shows a text box that populates with the date format: 

YYYY-mm-dd 

So that it works well with the tasks feature.

I also want the dialoge to accept typing (so if i type instead of pick a date I can enter text in the same box that shows the selected date).  if i type the following things should be supported.

below the text box have a checkbox default selected: Weekdays only

implement these options:


Core ones (definitely implement)

today

tomorrow

yesterday

next week → next Monday

If today is Monday, “next week” should mean next week’s Monday, not today.

this week → this week’s Monday (or configurable)

next <weekday> → next occurrence of that weekday

Examples: next monday, next fri, next sunday

in X days → today + X days

e.g. in 2 days, in 10 days

in X weeks → today + 7*X days

e.g. in 2 weeks

Nice-but-easy extras

Only if you want to expand later:

+N or +Nd → today + N days (e.g. +3, +7d)

-N or -Nd → today - N days

this <weekday> → the weekday in the current week (if not passed yet)


when enter is selected:
if the textbox has  date in it YYYY-mm-dd just insert that.
if it has text, evaluate the expression and arrive at the desired YYYY-mm-dd date and insert it.

if the text cannot be evaluated make it turn red don't allow enter to be pressed.
Esc just closes the dialoge and nothing is inserted.
this dialoge should be modal (must be either hit enter with valid date or Esc to close).

