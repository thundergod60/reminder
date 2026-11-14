# Reminder

This add-on lets you set reminders. When a reminder is due, NVDA plays a sound,
speaks the reminder and shows an alert dialog containing the text. The alert
stays on screen until it is dismissed with the OK button, just like a normal
Windows message box.

Reminders are saved, so they survive a restart of NVDA. If a reminder became
due while NVDA was not running, it is shown as soon as NVDA starts again.

## Commands

| Name | Default gesture |
| --- | --- |
| Add a reminder | NVDA+alt+r |
| Show the list of pending reminders | NVDA+alt+l |

You can change or remove these gestures in NVDA's Input Gestures dialog, in the
Reminder category.

## Adding a reminder

The Add reminder dialog contains a field for the reminder text, a When group
and a Repeat list:

- **In**: a number of minutes, hours or days from now.
- **At**: a time such as 14:30, or a date and time such as 2026-11-14 14:30.
  When only a time is entered and it has already passed today, the reminder is
  set for tomorrow.
- **Repeat**: Never, Every day, Every weekday, Every week or Every month. A
  repeating reminder is rescheduled automatically after it is shown, and it
  keeps repeating even if NVDA restarts.

The reminder text field is multi-line, so press Tab to reach the buttons and
then Enter to save the reminder.

## The alert

When a reminder is due, the alert offers two buttons: OK dismisses it, and
Snooze schedules the same text again after the default snooze time. A snoozed
reminder is a separate one-off reminder, so the repeat schedule of the original
reminder is not affected.

## The reminder list

The list shows every pending reminder sorted by the time it is due, together
with its repeat setting. Select a reminder and choose Edit to change its text,
time or repeat setting, or Delete to remove it. Pressing Enter on a reminder
also opens the Edit dialog, and the Add button creates another one.

## Settings

The Reminder category in NVDA's Settings dialog controls:

- whether a sound is played when a reminder is due;
- whether the reminder text is spoken;
- the sound file to play, with a Browse button and a Test sound button. Leave
  the field empty to use the chime included with the add-on;
- the default snooze time in minutes, used by the Snooze button.

If the selected sound file is missing or cannot be played, NVDA falls back to
the included chime and then to a beep.
        
