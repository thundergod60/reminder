# -*- coding: utf-8 -*-
"""reminder

An NVDA add-on that lets the user set one-off or repeating reminders. When a
reminder is due, NVDA plays a sound, speaks it and shows an alert dialog.
"""

import calendar
import datetime
import json
import logging
import os
import sys

import wx

import addonHandler
import globalPluginHandler
import globalVars
import gui
import gui.guiHelper
import gui.settingsDialogs
import nvwave
import tones
import ui
from scriptHandler import script

addonHandler.initTranslation()

log = logging.getLogger(__name__)

ADDON_DIR = os.path.dirname(os.path.abspath(__file__))
SOUND_PATH = os.path.join(ADDON_DIR, "sounds", "reminder.wav")
DATA_FILE_NAME = "reminders.json"
SETTINGS_FILE_NAME = "settings.json"
TIME_FORMAT = "%Y-%m-%dT%H:%M:%S"
DISPLAY_TIME_FORMAT = "%Y-%m-%d %H:%M"
CHECK_INTERVAL = 1000

REPEAT_NONE = "none"
REPEAT_DAILY = "daily"
REPEAT_WEEKDAYS = "weekdays"
REPEAT_WEEKLY = "weekly"
REPEAT_MONTHLY = "monthly"
REPEAT_KEYS = (REPEAT_NONE, REPEAT_DAILY, REPEAT_WEEKDAYS, REPEAT_WEEKLY, REPEAT_MONTHLY)

DEFAULT_SETTINGS = {
    "playSound": True,
    "speakReminder": True,
    "soundFile": "",
    "snoozeMinutes": 5,
}


def getRepeatLabel(key):
    labels = {
        REPEAT_NONE: _("Never"),
        REPEAT_DAILY: _("Every day"),
        REPEAT_WEEKDAYS: _("Every weekday"),
        REPEAT_WEEKLY: _("Every week"),
        REPEAT_MONTHLY: _("Every month"),
    }
    return labels.get(key, labels[REPEAT_NONE])


# Storage


def getDataDir():
    base = getattr(globalVars.appArgs, "configPath", None)
    if not base:
        base = os.path.join(os.path.expanduser("~"), "AppData", "Roaming", "nvda")
    directory = os.path.join(base, "reminder")
    try:
        os.makedirs(directory, exist_ok=True)
    except OSError:
        log.exception("Could not create the reminder data directory")
    return directory


def getDataFile(name):
    return os.path.join(getDataDir(), name)


def trace(message):
    """Writes a line to debug.log in the reminder data directory."""
    try:
        with open(getDataFile("debug.log"), "a", encoding="utf-8") as f:
            f.write("%s %s\n" % (datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"), message))
    except OSError:
        log.debugWarning("Could not write the reminder trace", exc_info=True)


def loadSettings():
    settings = dict(DEFAULT_SETTINGS)
    try:
        with open(getDataFile(SETTINGS_FILE_NAME), "r", encoding="utf-8") as f:
            stored = json.load(f)
    except FileNotFoundError:
        return settings
    except (OSError, ValueError):
        log.exception("Could not read the reminder settings")
        return settings
    if not isinstance(stored, dict):
        return settings
    if isinstance(stored.get("playSound"), bool):
        settings["playSound"] = stored["playSound"]
    if isinstance(stored.get("speakReminder"), bool):
        settings["speakReminder"] = stored["speakReminder"]
    if isinstance(stored.get("soundFile"), str):
        settings["soundFile"] = stored["soundFile"]
    minutes = stored.get("snoozeMinutes")
    if isinstance(minutes, int) and not isinstance(minutes, bool) and 1 <= minutes <= 1440:
        settings["snoozeMinutes"] = minutes
    return settings


def saveSettings(settings):
    try:
        with open(getDataFile(SETTINGS_FILE_NAME), "w", encoding="utf-8") as f:
            json.dump(settings, f, ensure_ascii=False, indent=1)
    except OSError:
        log.exception("Could not save the reminder settings")


def newId():
    return datetime.datetime.now().strftime("%Y%m%d%H%M%S%f")


def cleanReminder(entry):
    if not isinstance(entry, dict):
        return None
    message = entry.get("message")
    due = entry.get("due")
    if not isinstance(message, str) or not isinstance(due, str):
        return None
    try:
        datetime.datetime.strptime(due, TIME_FORMAT)
    except ValueError:
        return None
    repeat = entry.get("repeat")
    if repeat not in REPEAT_KEYS:
        repeat = REPEAT_NONE
    identifier = entry.get("id")
    if not isinstance(identifier, str) or not identifier:
        identifier = newId()
    return {"id": identifier, "message": message, "due": due, "repeat": repeat}


def loadReminders():
    reminders = []
    try:
        with open(getDataFile(DATA_FILE_NAME), "r", encoding="utf-8") as f:
            data = json.load(f)
    except FileNotFoundError:
        return reminders
    except (OSError, ValueError):
        log.exception("Could not read the stored reminders")
        return reminders
    entries = data.get("reminders") if isinstance(data, dict) else data
    if not isinstance(entries, list):
        return reminders
    for entry in entries:
        reminder = cleanReminder(entry)
        if reminder is not None:
            reminders.append(reminder)
    reminders.sort(key=lambda item: item["due"])
    return reminders


def saveReminders(reminders):
    try:
        with open(getDataFile(DATA_FILE_NAME), "w", encoding="utf-8") as f:
            json.dump(
                {
                    "version": 2,
                    "reminders": sorted(reminders, key=lambda item: item["due"]),
                },
                f,
                ensure_ascii=False,
                indent=1,
            )
    except OSError:
        log.exception("Could not save the reminders")


# Dates and repeats


def formatDue(due):
    try:
        return datetime.datetime.strptime(due, TIME_FORMAT).strftime(DISPLAY_TIME_FORMAT)
    except (TypeError, ValueError):
        return str(due)


def parseDue(due):
    try:
        return datetime.datetime.strptime(due, TIME_FORMAT)
    except (TypeError, ValueError):
        return None


def addMonths(value, months=1):
    year = value.year + (value.month - 1 + months) // 12
    month = (value.month - 1 + months) % 12 + 1
    day = min(value.day, calendar.monthrange(year, month)[1])
    return value.replace(year=year, month=month, day=day)


def advanceDue(value, repeat):
    if repeat == REPEAT_DAILY:
        return value + datetime.timedelta(days=1)
    if repeat == REPEAT_WEEKDAYS:
        nextValue = value + datetime.timedelta(days=1)
        while nextValue.weekday() >= 5:
            nextValue += datetime.timedelta(days=1)
        return nextValue
    if repeat == REPEAT_WEEKLY:
        return value + datetime.timedelta(days=7)
    if repeat == REPEAT_MONTHLY:
        return addMonths(value)
    return None


def nextDueAfter(value, repeat, now):
    if repeat == REPEAT_NONE:
        return None
    candidate = advanceDue(value, repeat)
    guard = 0
    while candidate is not None and candidate <= now and guard < 500:
        candidate = advanceDue(candidate, repeat)
        guard += 1
    return candidate


def describeRepeat(reminder):
    repeat = reminder.get("repeat", REPEAT_NONE)
    if repeat == REPEAT_NONE:
        return ""
    return " (%s)" % getRepeatLabel(repeat)


# Sound and messages


def playReminderSound(path=None):
    path = path or SOUND_PATH
    if path and os.path.isfile(path):
        try:
            nvwave.playWaveFile(path)
            return
        except Exception:
            log.exception("Could not play the reminder sound %s", path)
    try:
        tones.beep(880, 120)
        tones.beep(1174, 180)
    except Exception:
        log.exception("Could not play the fallback beeps")


def popupDialog(dialog, nested=False):
    """Shows a dialog the way NVDA shows its own dialogs.

    NVDA opens its settings, configuration profile and exit dialogs with Show()
    between prePopup() and postPopup(), and those stay responsive. ShowModal()
    runs a nested event loop which leaves NVDA unresponsive when the dialog is
    opened from a script, so these dialogs close themselves and report their
    result through callbacks instead of returning from ShowModal().

    A dialog opened from inside another dialog is nested: it keeps that dialog
    as its parent and is shown without suspending the main frame again.
    """
    mainFrame = getattr(gui, "mainFrame", None)
    if not nested:
        try:
            if mainFrame is not None:
                mainFrame.prePopup()
        except Exception:
            log.debugWarning("prePopup failed", exc_info=True)
    try:
        dialog.Show()
        trace(
            "popupDialog: shown %r shown=%s foreground=%r"
            % (dialog.GetTitle(), dialog.IsShown(), foregroundTitle())
        )
        wx.CallLater(500, logDialogState, dialog)
    except Exception:
        log.exception("Could not show the reminder dialog")
        trace("popupDialog: failed %r" % (sys.exc_info()[1],))
        dialog.Destroy()
        return False
    finally:
        if not nested:
            try:
                if mainFrame is not None:
                    mainFrame.postPopup()
            except Exception:
                log.debugWarning("postPopup failed", exc_info=True)
    return True


def raiseExisting(owner, attribute):
    """Brings an already open dialog back instead of opening a second one."""
    dialog = getattr(owner, attribute, None)
    if dialog is None:
        return False
    try:
        if dialog.IsShown():
            dialog.Raise()
            return True
    except RuntimeError:
        setattr(owner, attribute, None)
    return False


def foregroundTitle():
    try:
        import ctypes

        user32 = ctypes.windll.user32
        handle = user32.GetForegroundWindow()
        length = user32.GetWindowTextLengthW(handle)
        buffer = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(handle, buffer, length + 1)
        return buffer.value
    except Exception:
        return "?"


def logDialogState(dialog):
    """Reports on a dialog while it is on screen."""
    try:
        trace(
            "popupDialog: open shown=%s visible=%s foreground=%r"
            % (dialog.IsShown(), dialog.IsVisible(), foregroundTitle())
        )
    except Exception:
        trace("popupDialog: state check failed: %r" % (sys.exc_info()[1],))


def showAlert(messages, settings, onSnooze=None, onClosed=None):
    """Shows the alert for the given messages."""
    text = "\n\n".join(messages)
    if settings.get("playSound", True):
        playReminderSound(settings.get("soundFile") or SOUND_PATH)
    if settings.get("speakReminder", True):
        # Speak after the alert has opened, so the focus change to the dialog
        # does not interrupt the speech.
        wx.CallLater(250, ui.message, text)
    trace("alert: creating dialog for %d reminder(s)" % len(messages))
    dialog = AlertDialog(
        messages,
        settings.get("snoozeMinutes", 5),
        onSnooze=onSnooze,
        onClosed=onClosed,
    )
    if not popupDialog(dialog) and onClosed is not None:
        onClosed()


class AlertDialog(wx.Dialog):
    """The alert shown when one or more reminders are due."""

    def __init__(self, messages, snoozeMinutes, onSnooze=None, onClosed=None):
        title = _("Reminder") if len(messages) == 1 else _("Reminders")
        super().__init__(None, title=title)
        self.snoozed = False
        self._onSnooze = onSnooze
        self._onClosed = onClosed
        self._closed = False
        self.Bind(wx.EVT_SHOW, self.onShow)
        self.Bind(wx.EVT_CLOSE, self.onClose)

        mainSizer = wx.BoxSizer(wx.VERTICAL)
        mainSizer.Add(
            wx.StaticText(self, label=_("&Reminder:")),
            flag=wx.ALL,
            border=5,
        )
        self.textCtrl = wx.TextCtrl(
            self,
            value="\n\n".join(messages),
            style=wx.TE_MULTILINE | wx.TE_READONLY,
            size=(460, 130),
        )
        mainSizer.Add(
            self.textCtrl,
            proportion=1,
            flag=wx.EXPAND | wx.LEFT | wx.RIGHT,
            border=5,
        )

        buttonSizer = wx.BoxSizer(wx.HORIZONTAL)
        self.snoozeButton = wx.Button(
            self,
            label=_("&Snooze for %d minutes") % snoozeMinutes,
        )
        self.okButton = wx.Button(self, id=wx.ID_OK, label=_("OK"))
        buttonSizer.Add(self.snoozeButton)
        buttonSizer.Add(self.okButton, flag=wx.LEFT, border=5)
        mainSizer.Add(buttonSizer, flag=wx.ALIGN_RIGHT | wx.ALL, border=5)

        self.SetSizerAndFit(mainSizer)
        self.SetEscapeId(wx.ID_OK)
        self.okButton.SetDefault()
        self.Bind(wx.EVT_BUTTON, self.onSnooze, self.snoozeButton)
        self.Bind(wx.EVT_BUTTON, self.onOk, id=wx.ID_OK)
        self.CentreOnScreen()
        self.okButton.SetFocus()

    def onShow(self, event):
        if event.IsShown():
            self.Raise()
            self.okButton.SetFocus()
        event.Skip()

    def onSnooze(self, event):
        self.snoozed = True
        if self._onSnooze is not None:
            try:
                self._onSnooze()
            except Exception:
                log.exception("Could not snooze the reminder")
        self.close()

    def onOk(self, event):
        self.close()

    def onClose(self, event):
        self.close()

    def close(self):
        if self._closed:
            return
        self._closed = True
        try:
            if self._onClosed is not None:
                self._onClosed()
        finally:
            wx.CallAfter(self.Destroy)


# Dialogs


class AddReminderDialog(wx.Dialog):
    """Collects the text, due time and repetition of a reminder."""

    def __init__(self, parent=None, reminder=None, onSaved=None):
        super().__init__(
            parent,
            title=_("Edit reminder") if reminder else _("Add reminder"),
        )
        self.reminder = None
        self._editing = reminder
        self._onSaved = onSaved
        self._closed = False
        self.Bind(wx.EVT_SHOW, self.onShow)
        self.Bind(wx.EVT_CLOSE, self.onClose)

        mainSizer = wx.BoxSizer(wx.VERTICAL)

        mainSizer.Add(
            wx.StaticText(self, label=_("&Reminder text:")),
            flag=wx.ALL,
            border=5,
        )
        self.messageCtrl = wx.TextCtrl(self, style=wx.TE_MULTILINE, size=(460, 100))
        mainSizer.Add(
            self.messageCtrl,
            proportion=1,
            flag=wx.EXPAND | wx.LEFT | wx.RIGHT,
            border=5,
        )

        whenBox = wx.StaticBoxSizer(wx.VERTICAL, self, _("When"))
        box = whenBox.GetStaticBox()

        relativeSizer = wx.BoxSizer(wx.HORIZONTAL)
        self.relativeRadio = wx.RadioButton(box, label=_("&In"), style=wx.RB_GROUP)
        self.amountCtrl = wx.SpinCtrl(box, min=1, max=9999, initial=5, size=(90, -1))
        self.unitChoice = wx.Choice(box, choices=[_("minutes"), _("hours"), _("days")])
        self.unitChoice.SetSelection(0)
        relativeSizer.Add(self.relativeRadio, flag=wx.ALIGN_CENTER_VERTICAL)
        relativeSizer.Add(
            self.amountCtrl,
            flag=wx.LEFT | wx.ALIGN_CENTER_VERTICAL,
            border=5,
        )
        relativeSizer.Add(
            self.unitChoice,
            flag=wx.LEFT | wx.ALIGN_CENTER_VERTICAL,
            border=5,
        )
        whenBox.Add(relativeSizer, flag=wx.ALL, border=5)

        absoluteSizer = wx.BoxSizer(wx.HORIZONTAL)
        self.absoluteRadio = wx.RadioButton(box, label=_("&At"))
        self.atCtrl = wx.TextCtrl(box, size=(200, -1))
        absoluteSizer.Add(self.absoluteRadio, flag=wx.ALIGN_CENTER_VERTICAL)
        absoluteSizer.Add(
            self.atCtrl,
            flag=wx.LEFT | wx.ALIGN_CENTER_VERTICAL,
            border=5,
        )
        whenBox.Add(absoluteSizer, flag=wx.ALL, border=5)

        repeatSizer = wx.BoxSizer(wx.HORIZONTAL)
        repeatSizer.Add(
            wx.StaticText(box, label=_("&Repeat:")),
            flag=wx.ALIGN_CENTER_VERTICAL,
        )
        self.repeatChoice = wx.Choice(
            box,
            choices=[getRepeatLabel(key) for key in REPEAT_KEYS],
        )
        self.repeatChoice.SetSelection(0)
        repeatSizer.Add(
            self.repeatChoice,
            flag=wx.LEFT | wx.ALIGN_CENTER_VERTICAL,
            border=5,
        )
        whenBox.Add(repeatSizer, flag=wx.LEFT | wx.RIGHT | wx.BOTTOM, border=5)

        whenBox.Add(
            wx.StaticText(
                box,
                label=_(
                    "Enter a time such as 14:30, or a date and time\n"
                    "such as 2026-11-14 14:30. A time that has already\n"
                    "passed today is used for tomorrow."
                ),
            ),
            flag=wx.LEFT | wx.RIGHT | wx.BOTTOM,
            border=5,
        )
        mainSizer.Add(whenBox, flag=wx.EXPAND | wx.ALL, border=5)

        self.errorText = wx.StaticText(self, label="", size=(460, -1))
        mainSizer.Add(self.errorText, flag=wx.LEFT | wx.RIGHT, border=5)

        mainSizer.Add(
            self.CreateButtonSizer(wx.OK | wx.CANCEL),
            flag=wx.EXPAND | wx.ALL,
            border=5,
        )

        self.SetSizerAndFit(mainSizer)
        self.SetEscapeId(wx.ID_CANCEL)

        if reminder:
            self.messageCtrl.SetValue(reminder.get("message", ""))
            self.relativeRadio.SetValue(False)
            self.absoluteRadio.SetValue(True)
            self.atCtrl.SetValue(formatDue(reminder.get("due", "")))
            repeat = reminder.get("repeat", REPEAT_NONE)
            try:
                self.repeatChoice.SetSelection(REPEAT_KEYS.index(repeat))
            except ValueError:
                self.repeatChoice.SetSelection(0)
        else:
            self.relativeRadio.SetValue(True)

        self.relativeRadio.Bind(wx.EVT_RADIOBUTTON, self.onWhenChanged)
        self.absoluteRadio.Bind(wx.EVT_RADIOBUTTON, self.onWhenChanged)
        self.Bind(wx.EVT_BUTTON, self.onOk, id=wx.ID_OK)
        self.Bind(wx.EVT_BUTTON, self.onCancel, id=wx.ID_CANCEL)
        self.updateWhenControls()
        self.CentreOnScreen()
        self.messageCtrl.SetFocus()
        self.messageCtrl.SetInsertionPointEnd()

    def onShow(self, event):
        if event.IsShown():
            self.Raise()
            self.messageCtrl.SetFocus()
        event.Skip()

    def updateWhenControls(self):
        relative = self.relativeRadio.GetValue()
        self.amountCtrl.Enable(relative)
        self.unitChoice.Enable(relative)
        self.atCtrl.Enable(not relative)

    def onWhenChanged(self, event):
        self.updateWhenControls()
        if self.absoluteRadio.GetValue():
            self.atCtrl.SetFocus()
        else:
            self.amountCtrl.SetFocus()

    def showError(self, text):
        """Reports a problem in the dialog itself, without another dialog."""
        self.errorText.SetLabelText(text)
        self.errorText.Wrap(460)
        self.Fit()
        ui.message(text)

    def close(self):
        """Destroys the dialog once the current event has been handled."""
        if self._closed:
            return
        self._closed = True
        wx.CallAfter(self.Destroy)

    def onCancel(self, event):
        self.close()

    def onClose(self, event):
        self.close()

    def onOk(self, event):
        message = self.messageCtrl.GetValue().strip()
        if not message:
            self.showError(_("Please enter the text of the reminder."))
            self.messageCtrl.SetFocus()
            return
        due = self.getDueTime()
        if due is None:
            return
        selection = self.repeatChoice.GetSelection()
        if selection == wx.NOT_FOUND:
            selection = 0
        identifier = None
        if self._editing:
            identifier = self._editing.get("id")
        self.reminder = {
            "id": identifier or newId(),
            "message": message,
            "due": due.strftime(TIME_FORMAT),
            "repeat": REPEAT_KEYS[selection],
        }
        if self._onSaved is not None:
            try:
                self._onSaved(self.reminder)
            except Exception:
                log.exception("Could not save the reminder")
        self.close()

    def getDueTime(self):
        now = datetime.datetime.now()
        if self.relativeRadio.GetValue():
            amount = self.amountCtrl.GetValue()
            if amount <= 0:
                self.showError(
                    _("Please enter a number of minutes, hours or days greater than zero.")
                )
                self.amountCtrl.SetFocus()
                return None
            unit = self.unitChoice.GetSelection()
            if unit == 1:
                delta = datetime.timedelta(hours=amount)
            elif unit == 2:
                delta = datetime.timedelta(days=amount)
            else:
                delta = datetime.timedelta(minutes=amount)
            return now + delta
        text = self.atCtrl.GetValue().strip()
        if not text:
            self.showError(
                _("Please enter a time, for example 14:30 or 2026-11-14 14:30.")
            )
            self.atCtrl.SetFocus()
            return None
        for timeFormat, isDate in (
            ("%H:%M", False),
            ("%Y-%m-%d %H:%M", True),
            ("%Y/%m/%d %H:%M", True),
        ):
            try:
                parsed = datetime.datetime.strptime(text, timeFormat)
            except ValueError:
                continue
            if not isDate:
                due = now.replace(
                    hour=parsed.hour,
                    minute=parsed.minute,
                    second=0,
                    microsecond=0,
                )
                if due <= now:
                    due += datetime.timedelta(days=1)
                return due
            due = parsed.replace(second=0, microsecond=0)
            if due <= now:
                self.showError(
                    _("That time is in the past. Please enter a time in the future.")
                )
                self.atCtrl.SetFocus()
                return None
            return due
        self.showError(
            _(
                "The time could not be understood. Use 14:30 for a time, "
                "or 2026-11-14 14:30 for a date and time."
            )
        )
        self.atCtrl.SetFocus()
        return None


class RemindersDialog(wx.Dialog):
    """Lists the pending reminders and lets the user add, edit or delete them."""

    def __init__(self, plugin):
        super().__init__(None, title=_("Reminders"))
        self.plugin = plugin
        self.reminders = []
        self._editor = None
        self.Bind(wx.EVT_SHOW, self.onShow)
        self.Bind(wx.EVT_CLOSE, self.onClose)

        mainSizer = wx.BoxSizer(wx.VERTICAL)
        mainSizer.Add(
            wx.StaticText(self, label=_("&Pending reminders:")),
            flag=wx.ALL,
            border=5,
        )
        self.listBox = wx.ListBox(self, style=wx.LB_SINGLE, size=(520, 220))
        mainSizer.Add(
            self.listBox,
            proportion=1,
            flag=wx.EXPAND | wx.LEFT | wx.RIGHT,
            border=5,
        )

        buttonSizer = wx.BoxSizer(wx.HORIZONTAL)
        self.addButton = wx.Button(self, label=_("&Add..."))
        self.editButton = wx.Button(self, label=_("&Edit..."))
        self.deleteButton = wx.Button(self, label=_("&Delete"))
        self.closeButton = wx.Button(self, id=wx.ID_CLOSE, label=_("Close"))
        for button in (
            self.addButton,
            self.editButton,
            self.deleteButton,
            self.closeButton,
        ):
            buttonSizer.Add(button, flag=wx.LEFT, border=5)
        mainSizer.Add(buttonSizer, flag=wx.ALIGN_RIGHT | wx.ALL, border=5)

        self.SetSizerAndFit(mainSizer)
        self.SetEscapeId(wx.ID_CLOSE)
        self.closeButton.SetDefault()

        self.Bind(wx.EVT_BUTTON, self.onAdd, self.addButton)
        self.Bind(wx.EVT_BUTTON, self.onEdit, self.editButton)
        self.Bind(wx.EVT_BUTTON, self.onDelete, self.deleteButton)
        self.Bind(wx.EVT_BUTTON, self.onClose, id=wx.ID_CLOSE)
        self.listBox.Bind(wx.EVT_LISTBOX_DCLICK, self.onEdit)

        self.refresh()
        self.listBox.SetFocus()

    def onShow(self, event):
        if event.IsShown():
            self.Raise()
            self.listBox.SetFocus()
        event.Skip()

    def refresh(self):
        self.reminders = sorted(self.plugin.reminders, key=lambda item: item["due"])
        self.listBox.Clear()
        for reminder in self.reminders:
            self.listBox.Append(
                "%s - %s%s"
                % (
                    formatDue(reminder["due"]),
                    reminder["message"],
                    describeRepeat(reminder),
                )
            )
        hasReminders = bool(self.reminders)
        if hasReminders:
            self.listBox.SetSelection(0)
        self.editButton.Enable(hasReminders)
        self.deleteButton.Enable(hasReminders)

    def getSelection(self):
        index = self.listBox.GetSelection()
        if index == wx.NOT_FOUND or index >= len(self.reminders):
            return None
        return self.reminders[index]

    def openEditor(self, reminder):
        def saved(new):
            if reminder is None:
                self.plugin.addReminder(new)
            else:
                self.plugin.replaceReminder(reminder, new)
            self.refresh()
            self.listBox.SetFocus()

        dialog = AddReminderDialog(self, reminder, onSaved=saved)
        self._editor = dialog
        popupDialog(dialog, nested=True)

    def onAdd(self, event):
        self.openEditor(None)

    def onEdit(self, event=None):
        reminder = self.getSelection()
        if reminder is None:
            ui.message(_("Select a reminder to edit."))
            return
        self.openEditor(reminder)

    def onDelete(self, event):
        reminder = self.getSelection()
        if reminder is None:
            ui.message(_("Select a reminder to delete."))
            return
        self.plugin.removeReminder(reminder)
        self.refresh()
        ui.message(_("Reminder deleted"))
        self.listBox.SetFocus()

    def onClose(self, event):
        wx.CallAfter(self.Destroy)


# Settings


class ReminderSettingsPanel(gui.settingsDialogs.SettingsPanel):
    title = _("Reminder")

    def makeSettings(self, settingsSizer):
        sHelper = gui.guiHelper.BoxSizerHelper(self, sizer=settingsSizer)

        self.playSoundCheckBox = sHelper.addItem(
            wx.CheckBox(self, label=_("Play a &sound when a reminder is due"))
        )
        self.speakCheckBox = sHelper.addItem(
            wx.CheckBox(self, label=_("&Speak the reminder text"))
        )
        self.soundCtrl = sHelper.addLabeledControl(
            _("Sound &file (leave empty for the default chime):"),
            wx.TextCtrl,
        )
        buttonsSizer = wx.BoxSizer(wx.HORIZONTAL)
        self.browseButton = wx.Button(self, label=_("&Browse..."))
        self.testButton = wx.Button(self, label=_("&Test sound"))
        buttonsSizer.Add(self.browseButton)
        buttonsSizer.Add(self.testButton, flag=wx.LEFT, border=5)
        sHelper.addItem(buttonsSizer)
        self.snoozeSpin = sHelper.addLabeledControl(
            _("Default &snooze time (minutes):"),
            wx.SpinCtrl,
            min=1,
            max=1440,
            initial=5,
        )

        self.browseButton.Bind(wx.EVT_BUTTON, self.onBrowse)
        self.testButton.Bind(wx.EVT_BUTTON, self.onTestSound)
        self.soundCtrl.Bind(wx.EVT_TEXT, self.onSoundChanged)
        self.soundCtrl.Bind(wx.EVT_KILL_FOCUS, self.onSoundChanged)

        settings = loadSettings()
        self.playSoundCheckBox.SetValue(settings["playSound"])
        self.speakCheckBox.SetValue(settings["speakReminder"])
        self.soundCtrl.SetValue(settings["soundFile"])
        self.snoozeSpin.SetValue(settings["snoozeMinutes"])
        self.updateSoundControls()

    def updateSoundControls(self):
        hasSound = self.playSoundCheckBox.GetValue()
        self.soundCtrl.Enable(hasSound)
        self.browseButton.Enable(hasSound)
        self.testButton.Enable(hasSound)

    def onSoundChanged(self, event):
        self.updateSoundControls()
        event.Skip()

    def onBrowse(self, event):
        current = self.soundCtrl.GetValue().strip()
        directory = getDataDir()
        if current:
            currentDirectory = os.path.dirname(current)
            if os.path.isdir(currentDirectory):
                directory = currentDirectory
        dialog = wx.FileDialog(
            self,
            _("Select a sound file"),
            defaultDir=directory,
            wildcard=_("Wave files (*.wav)|*.wav"),
            style=wx.FD_OPEN | wx.FD_FILE_MUST_EXIST,
        )
        try:
            if dialog.ShowModal() == wx.ID_OK:
                self.soundCtrl.SetValue(dialog.GetPath())
                self.updateSoundControls()
        finally:
            dialog.Destroy()

    def onTestSound(self, event):
        playReminderSound(self.soundCtrl.GetValue().strip() or SOUND_PATH)

    def onSave(self):
        saveSettings(
            {
                "playSound": self.playSoundCheckBox.GetValue(),
                "speakReminder": self.speakCheckBox.GetValue(),
                "soundFile": self.soundCtrl.GetValue().strip(),
                "snoozeMinutes": self.snoozeSpin.GetValue(),
            }
        )


trace("module imported from %s" % ADDON_DIR)


class GlobalPlugin(globalPluginHandler.GlobalPlugin):
    scriptCategory = _("Reminder")

    def __init__(self):
        super().__init__()
        self._reminders = []
        self._alerting = False
        self._timer = None
        self._timerId = int(wx.NewIdRef()) if hasattr(wx, "NewIdRef") else wx.NewId()
        if globalVars.appArgs.secure:
            trace("plugin not started: secure mode")
            return
        self._reminders = loadReminders()
        trace("plugin started with %d reminder(s)" % len(self._reminders))
        self.registerSettingsPanel()
        self.startTimer()

    @property
    def reminders(self):
        return self._reminders

    def registerSettingsPanel(self):
        try:
            categories = gui.settingsDialogs.NVDASettingsDialog.categoryClasses
            if ReminderSettingsPanel not in categories:
                categories.append(ReminderSettingsPanel)
            trace("settings panel registered")
        except Exception:
            log.exception("Could not register the reminder settings panel")
            trace("settings panel registration failed: %r" % (sys.exc_info()[1],))

    def unregisterSettingsPanel(self):
        try:
            categories = gui.settingsDialogs.NVDASettingsDialog.categoryClasses
            if ReminderSettingsPanel in categories:
                categories.remove(ReminderSettingsPanel)
        except Exception:
            log.exception("Could not unregister the reminder settings panel")

    def startTimer(self):
        try:
            self._timer = wx.Timer(gui.mainFrame, self._timerId)
            gui.mainFrame.Bind(wx.EVT_TIMER, self.onTimer, id=self._timerId)
            self._timer.Start(CHECK_INTERVAL)
        except Exception:
            log.exception("Could not start the reminder timer")

    def stopTimer(self):
        if self._timer is None:
            return
        try:
            self._timer.Stop()
        except Exception:
            log.exception("Could not stop the reminder timer")
        try:
            gui.mainFrame.Unbind(wx.EVT_TIMER, id=self._timerId)
        except Exception:
            log.exception("Could not unbind the reminder timer")
        self._timer = None

    def onTimer(self, event):
        if self._alerting:
            return
        now = datetime.datetime.now()
        due = []
        remaining = []
        for reminder in self._reminders:
            dueTime = parseDue(reminder["due"])
            if dueTime is None:
                continue
            if dueTime > now:
                remaining.append(reminder)
                continue
            due.append(reminder)
            nextTime = nextDueAfter(
                dueTime,
                reminder.get("repeat", REPEAT_NONE),
                now,
            )
            if nextTime is not None:
                reminder["due"] = nextTime.strftime(TIME_FORMAT)
                remaining.append(reminder)
        if not due:
            return
        trace("due reminders: %d" % len(due))
        self._alerting = True
        self._reminders = remaining
        saveReminders(self._reminders)
        wx.CallAfter(self.showAlerts, due)

    def showAlerts(self, reminders):
        settings = loadSettings()

        def snooze():
            self.snooze(reminders, settings)

        def finished():
            self._alerting = False

        try:
            showAlert(
                [reminder["message"] for reminder in reminders],
                settings,
                onSnooze=snooze,
                onClosed=finished,
            )
        except Exception:
            log.exception("Could not show the reminder alert")
            self._alerting = False

    def snooze(self, reminders, settings):
        minutes = settings.get("snoozeMinutes", 5) or 5
        due = (datetime.datetime.now() + datetime.timedelta(minutes=minutes)).strftime(
            TIME_FORMAT
        )
        for reminder in reminders:
            self._reminders.append(
                {
                    "id": newId(),
                    "message": reminder["message"],
                    "due": due,
                    "repeat": REPEAT_NONE,
                }
            )
        self._reminders.sort(key=lambda item: item["due"])
        saveReminders(self._reminders)
        ui.message(_("Snoozed for %d minutes") % minutes)

    def addReminder(self, reminder):
        self._reminders.append(reminder)
        self._reminders.sort(key=lambda item: item["due"])
        saveReminders(self._reminders)
        ui.message(_("Reminder set for %s") % formatDue(reminder["due"]))

    def replaceReminder(self, old, new):
        try:
            index = self._reminders.index(old)
        except ValueError:
            self.addReminder(new)
            return
        self._reminders[index] = new
        self._reminders.sort(key=lambda item: item["due"])
        saveReminders(self._reminders)
        ui.message(_("Reminder updated"))

    def removeReminder(self, reminder):
        try:
            self._reminders.remove(reminder)
        except ValueError:
            return
        saveReminders(self._reminders)

    @script(
        description=_("Shows the dialog for adding a new reminder."),
        gesture="kb:NVDA+alt+r",
    )
    def script_addReminder(self, gesture):
        trace("script_addReminder entered")
        try:
            if raiseExisting(self, "_addDialog"):
                return
            dialog = AddReminderDialog(onSaved=self.addReminder)
            trace(
                "add dialog built: size=%s position=%s"
                % (dialog.GetSize(), dialog.GetPosition())
            )
            self._addDialog = dialog
            popupDialog(dialog)
        except Exception:
            log.exception("Reminder: the add dialog failed")
            trace("script_addReminder failed: %r" % (sys.exc_info()[1],))

    @script(
        description=_("Shows the list of pending reminders."),
        gesture="kb:NVDA+alt+l",
    )
    def script_showReminders(self, gesture):
        trace("script_showReminders entered")
        try:
            if raiseExisting(self, "_listDialog"):
                return
            dialog = RemindersDialog(self)
            trace(
                "list dialog built: size=%s position=%s"
                % (dialog.GetSize(), dialog.GetPosition())
            )
            self._listDialog = dialog
            popupDialog(dialog)
        except Exception:
            log.exception("Reminder: the list dialog failed")
            trace("script_showReminders failed: %r" % (sys.exc_info()[1],))

    def terminate(self):
        self.stopTimer()
        self.unregisterSettingsPanel()
        if getattr(self, "_reminders", None):
            saveReminders(self._reminders)
