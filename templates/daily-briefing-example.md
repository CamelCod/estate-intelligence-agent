# Example: Daily Briefing

## Input

Customer: Ahmed Al Mansouri
Property: Villa, Jumeirah, 6 cameras (entrance, driveway, garden x2, side gate, garage)
Staff roster:
  - Mariam (cleaner): expected Mon/Wed/Fri, 8am–5pm
  - Khalid (driver): expected daily, 7am–7pm

Camera feed summary (Thursday):
  - 07:02 — Khalid arrives, main entrance
  - 07:58 — Amazon delivery van, driveway. Package left at door.
  - 09:44 — Package moved by Khalid into house
  - 11:30 — Side gate camera offline (detected)
  - 13:21 — Side gate camera back online
  - 19:08 — Khalid departs, main entrance
  - 23:00 — End of day, no further events

---

## Expected Output (WhatsApp Briefing)

Good evening, Ahmed. Here's your home briefing for Thursday, 3 April.

Khalid arrived at 7:02am and left at 7:08pm — a full day, as expected. Today is not Mariam's scheduled day.

An Amazon delivery arrived at 7:58am and was brought inside by Khalid at 9:44am. No other deliveries or visitors today.

One thing to note: the side gate camera went offline at 11:30am and came back online at 1:21pm. The gap was about 1 hour 50 minutes. This may be a connectivity issue worth checking — I've flagged that camera for review.

Tip: You can ask me anything from today — for example, "What time did Khalid leave?" or "Was anyone in the garden today?"

Anything specific you'd like to know about today? 🏡

---

## Notes
- Total words: 162 (within 250 limit)
- No markdown, no bullet symbols
- Staff accountability covered
- Anomaly (camera offline) flagged without alarm
- Proactive tip included
- Ends with open question
- Word "security" not used