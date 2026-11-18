# Cornell Course Watcher

Checks the Cornell Class Roster API every 10 minutes and emails you when a course you're watching opens up.

## 1. Pick your courses

Edit `watchlist.json`:

```json
{
  "roster": "SP27",
  "count_waitlist_as_open": false,
  "watch": [
    "CS 4780",
    "CS 3110 DIS 201"
  ]
}
```

- `"CS 4780"` watches the **whole course**. It only counts as open when you could actually enroll: some enrollment group has an open seat in *every* required part (lecture **and** discussion/lab). An open discussion with a full lecture won't trigger it.
- `"CS 3110 DIS 201"` watches **one specific section**. The format is component + section number, as shown on the roster (LEC 001, DIS 201, LAB 403...).
- `"roster"` is the term code (SP27 = Spring 2027, FA27 = Fall 2027). `"auto"` uses the newest roster.
- Set `count_waitlist_as_open` to `true` if you also want an alert when a waitlist opens.

You get one email when something opens. If it closes and opens again, you get another.

## 2. Set up email (Gmail)

1. Turn on 2-Step Verification for your Google account.
2. Create an **App Password**: Google Account → Security → App passwords. Copy the 16-character password.

(For other providers, also set `SMTP_HOST` and `SMTP_PORT`. Port 465 uses SSL; anything else uses STARTTLS.)

## 3a. Run it on GitHub (free, works when your laptop is off). Recommended.

1. Create a new GitHub repo and upload these files. Keep the `.github/workflows/watch.yml` path exactly as it is.
2. Go to repo **Settings → Secrets and variables → Actions → New repository secret** and add:
   - `SMTP_USER`: your Gmail address
   - `SMTP_PASSWORD`: the app password
   - `EMAIL_TO`: where alerts should go (e.g. your phone's email or your netid@cornell.edu)
3. Go to the **Actions** tab, enable workflows if asked, open "Watch Cornell courses" and click **Run workflow** to test it. The log shows the status of every course.

Notes:
- **Public repo:** Actions minutes are unlimited and free. Your password stays private in Secrets.
- **Private repo:** you get 2,000 free minutes/month, and every 10 minutes uses ~4,300. In `watch.yml`, change the cron to `"*/30 * * * *"`.
- GitHub's scheduler can run a few minutes late during busy periods.
- On public repos, GitHub pauses scheduled workflows after 60 days with no repo activity. Re-enable it in the Actions tab if that happens.

## 3b. Or run it on your own computer

```bash
export SMTP_USER="you@gmail.com"
export SMTP_PASSWORD="your16charapppassword"
export EMAIL_TO="you@gmail.com"

python3 course_watcher.py --test-email   # check email works
python3 course_watcher.py --status       # see current statuses, no email
python3 course_watcher.py                # real check
```

To run it every 10 minutes on Mac/Linux, run `crontab -e` and add a line like:

```
*/10 * * * * cd /path/to/cornell-course-watcher && SMTP_USER=you@gmail.com SMTP_PASSWORD=xxxx EMAIL_TO=you@gmail.com /usr/bin/python3 course_watcher.py >> watcher.log 2>&1
```

This only runs while your computer is awake.

## How it works

- Calls `https://classes.cornell.edu/api/2.0/search/classes.json?roster=SP27&subject=CS`, once per subject, and reads each section's `openStatus` (`O` open, `C` closed, `W` waitlist).
- Waits over 1 second between requests, per Cornell's API usage policy.
- Remembers what was open in `state.json` so it emails only when a status changes.
- Uses only the Python standard library, so there's nothing to install.
