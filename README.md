# Yap

**Read this first (important):** Yap is a small app made by me, Federico, not by a company or an Apple-verified developer. So the first time you open it, macOS will warn you that it is from an "unidentified developer" and may not let you open it with a normal double-click. This is expected and normal for an app shared directly like this.

To open it the first time:

1. Right-click (or Control-click) `Yap.app` and choose **Open**, then click **Open** again in the box that appears.
2. If macOS still blocks it, go to **System Settings > Privacy & Security**, scroll down, and click **Open Anyway** next to the message about Yap.

You only have to do this once. After that it opens normally.

---

## What Yap does

Yap turns your voice into clean, ready-to-use text anywhere on your Mac. You hold a key, talk, and let go, and your words appear right where your cursor is: cleaned up, summarized, or drafted into an email. It can also rewrite messy text you have already typed. You stay in control, because every mode is just a plain-language instruction you can edit.

## Install

1. Unzip the download. You will get `Yap.app`.
2. Drag `Yap.app` into your **Applications** folder.
3. Open it using the right-click method described at the top.
4. The first time, Yap walks you through granting a few permissions and adding your key.

## Get your free key

Yap uses Google's Gemini, and each person uses their own key, which stays on your Mac.

1. Get a free key at https://aistudio.google.com/apikey
2. Paste it into Yap when it asks during setup.

Optional: you can enable billing on your Google account for faster, higher-limit responses, or use an OpenAI key instead. Both are set in Settings.

## Permissions, and why each one is needed

Yap asks for three macOS permissions during setup. All three are required for the main feature to work:

- **Microphone**: to hear you while you talk.
- **Input Monitoring**: to detect the shortcut key you hold.
- **Accessibility**: to paste the finished text into whatever app you are in.

Grant them when asked, or later in System Settings > Privacy & Security. If a step asks you to quit and reopen the app, do it; that only happens once.

## How to use it

- Hold **Right Option** and tap an arrow key to pick a mode, talk, then release. The menu shows which arrow does what, for example a clean transcription, a summary with your tasks, or a drafted email.
- To rewrite text you have already typed, select it and press **Control + Option + /**.
- Click the Yap icon in the menu bar and open **Settings** to edit any prompt or change the shortcuts.

## Privacy

Yap runs on your Mac. When you record, only your audio, or the text you select, is sent over a secure connection to the provider you chose (Google Gemini or OpenAI) to be turned into text. Your API key is stored locally on your Mac and is never shared with me or anyone else. Nothing else is collected.

## Good to know

- Yap is unsigned, which is why you see the first-open warning above.
- It is provided as-is, for personal use. Enjoy, and tell me if something breaks.
