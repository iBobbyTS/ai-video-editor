# Instagram Upload Setup (Graph API)

## Overview

Upload photos and reels to your Instagram Business/Creator account using the Meta Graph API v21.0 Content Publishing endpoints.

**Credentials file:** `instagram_credentials.json` (git-ignored, never committed)

## Architecture

```
Meta Developer App (ID: YOUR_APP_ID)
│
├── Facebook Page: YourPage (ID: YOUR_PAGE_ID)
│   └── Linked Instagram Business Account: @your_account (ID: YOUR_IG_USER_ID)
│
└── Business Manager (ID: YOUR_BUSINESS_MANAGER_ID)
    ├── Owns Page: YourPage
    └── Owns IG Account: @your_account
```

**Token chain:**

```
Short-Lived User Token  (1 hour)
    ↓  exchange via app_secret
Long-Lived User Token   (60 days)
    ↓  request page token
Page Access Token        (never expires)  ← this is what we store
    ↓  used to call
Instagram Graph API      (publish photos, reels, stories)
```

## Prerequisites

Before starting, you need:

1. **Facebook personal account** (to own the Developer App)
2. **Facebook Page** (to link Instagram — personal profiles don't work)
3. **Instagram Professional account** (Business or Creator — not Personal)
4. **Meta Developer App** with Instagram Graph API product added

## Step-by-Step Setup

### 1. Create a Meta Developer App

1. Go to: https://developers.facebook.com/apps/
2. Click **Create App**
3. Choose **Business** type
4. Name it (e.g. "MyProject Upload")
5. Note your **App ID** — you'll need it later

### 2. Add Instagram Graph API Product

1. In your app dashboard: https://developers.facebook.com/apps/YOUR_APP_ID/dashboard/
2. Click **Add Product** in the left sidebar
3. Find **Instagram Graph API** → click **Set Up**
4. This enables the Instagram publishing endpoints

### 3. Create a Facebook Page

If you don't have one already:

1. Go to: https://www.facebook.com/pages/create
2. Choose **Creator** or **Brand** category
3. Name it (e.g. "MyPage")
4. Complete the setup

> **Important:** A Facebook Page is **required** — Instagram Graph API only works through a Page, not a personal profile.

### 4. Switch Instagram to Professional Account

In the Instagram app:

1. Go to **Settings** → **Account** → **Switch to Professional Account**
2. Choose **Creator** or **Business**
3. Select a category (e.g. "Digital Creator")
4. Complete the setup

### 5. Link Instagram to Facebook Page

This is the critical step — the Instagram account must be connected to the Facebook Page via Business Manager.

**Method A — From Business Manager (recommended):**

1. Go to: https://business.facebook.com/latest/settings/pages/ → select your Page
2. Click **Connected assets** tab
3. Click **Add asset** → **Instagram account** → select your IG account

OR

1. Go to: https://business.facebook.com/latest/settings/instagram_account/ → select your IG account
2. Click **Connected assets** tab
3. Click **Add asset** → **Page** → select your Page

**Method B — From Instagram app:**

1. Settings → Account → Linked Accounts → Facebook
2. Choose your Facebook **Page** (not personal profile)

**Method C — From Facebook Page:**

1. Go to your Page → Settings → Linked Accounts → Instagram
2. Click **Connect Account** → log in with Instagram credentials

**Verify the link works:**

```bash
# Query the page for its linked IG account
curl -s "https://graph.facebook.com/v21.0/YOUR_PAGE_ID?fields=instagram_business_account&access_token=YOUR_TOKEN" | python3 -m json.tool
```

Should return:
```json
{
  "instagram_business_account": {
    "id": "YOUR_IG_USER_ID"
  },
  "id": "YOUR_PAGE_ID"
}
```

### 6. Generate User Access Token

1. Go to **Graph API Explorer**: https://developers.facebook.com/tools/explorer/
2. Select your app from the dropdown (top-right)
3. Click **Generate Access Token** → choose **Get User Access Token**
4. Check these **5 permissions**:
   - `public_profile`
   - `pages_show_list`
   - `pages_read_engagement`
   - `instagram_basic`
   - `instagram_content_publish`
5. Click **Generate Access Token**
6. Authorize in the popup

> **Note:** Choose "Get **User** Access Token", not "Get App Token". App Tokens can't publish to Instagram.

### 7. Exchange for Long-Lived Token

The token from Step 6 expires in ~1 hour. Exchange it for a 60-day token:

```bash
curl -s "https://graph.facebook.com/v21.0/oauth/access_token?\
grant_type=fb_exchange_token&\
client_id=YOUR_APP_ID&\
client_secret=YOUR_APP_SECRET&\
fb_exchange_token=YOUR_SHORT_LIVED_TOKEN" | python3 -m json.tool
```

**Where to find the App Secret:** https://developers.facebook.com/apps/YOUR_APP_ID/settings/basic/ → click "Show" next to App Secret.

Returns:
```json
{
  "access_token": "EAAR3pop...long_token...",
  "token_type": "bearer",
  "expires_in": 5184000
}
```

### 8. Get Permanent Page Access Token

Use the long-lived user token to request a Page Access Token (never expires):

```bash
curl -s "https://graph.facebook.com/v21.0/YOUR_PAGE_ID?fields=access_token&\
access_token=YOUR_LONG_LIVED_USER_TOKEN" | python3 -m json.tool
```

Returns:
```json
{
  "access_token": "EAAR3pop...page_token...",
  "id": "YOUR_PAGE_ID"
}
```

> **This page token never expires** — it's derived from a long-lived user token and inherits permanent validity when the user has a role on the page.

### 9. Save Credentials

Create `instagram_credentials.json` in the project root:

```json
{
  "app_id": "YOUR_META_APP_ID",
  "ig_user_id": "YOUR_IG_USER_ID",
  "page_id": "YOUR_PAGE_ID",
  "page_name": "YourPage",
  "page_access_token": "YOUR_PERMANENT_PAGE_ACCESS_TOKEN"
}
```

Verify it's in `.gitignore`:

```bash
grep instagram_credentials .gitignore
# Should show: instagram_credentials.json
```

### 10. Verify Everything Works

```bash
# Test: query your IG account info
curl -s "https://graph.facebook.com/v21.0/YOUR_IG_USER_ID?\
fields=id,username,name,followers_count,media_count&\
access_token=$(python3 -c 'import json; print(json.load(open("instagram_credentials.json"))["page_access_token"])')" \
| python3 -m json.tool
```

Expected:
```json
{
  "id": "YOUR_IG_USER_ID",
  "username": "your_account",
  "name": "YourPage",
  "followers_count": 0,
  "media_count": 0
}
```

## Token Summary

| Token type | Lifetime | How to get | Use case |
|-----------|----------|-----------|----------|
| Short-lived User Token | ~1 hour | Graph API Explorer | Starting point only |
| Long-lived User Token | 60 days | Exchange short-lived via `/oauth/access_token` | Intermediate step |
| Page Access Token | **Never expires** | Request via `/{page_id}?fields=access_token` with long-lived user token | **This is what we store and use** |

## API Endpoints Used

### Photo Publishing (two-step)

```bash
# Step 1: Create media container
curl -X POST "https://graph.facebook.com/v21.0/{ig_user_id}/media" \
  -d "image_url=https://example.com/photo.jpg" \
  -d "caption=My photo caption" \
  -d "access_token={page_access_token}"

# Step 2: Publish
curl -X POST "https://graph.facebook.com/v21.0/{ig_user_id}/media_publish" \
  -d "creation_id={container_id_from_step_1}" \
  -d "access_token={page_access_token}"
```

### Reel Publishing (two-step)

```bash
# Step 1: Create reel container
curl -X POST "https://graph.facebook.com/v21.0/{ig_user_id}/media" \
  -d "video_url=https://example.com/reel.mp4" \
  -d "caption=My reel caption" \
  -d "media_type=REELS" \
  -d "access_token={page_access_token}"

# Step 2: Wait for processing, then publish
curl -X POST "https://graph.facebook.com/v21.0/{ig_user_id}/media_publish" \
  -d "creation_id={container_id_from_step_1}" \
  -d "access_token={page_access_token}"
```

> **Note:** `image_url` / `video_url` must be a **publicly accessible URL**. For local files, either host temporarily (e.g. `python3 -m http.server`) or use the Resumable Upload API.

### Check Container Status

```bash
curl -s "https://graph.facebook.com/v21.0/{container_id}?\
fields=status_code,status&access_token={page_access_token}"
```

Status codes: `EXPIRED`, `ERROR`, `FINISHED`, `IN_PROGRESS`, `PUBLISHED`

## Rate Limits

- **Content Publishing:** 50 posts per 24-hour rolling window per IG account
- **API calls:** 200 calls per hour per user (across all endpoints)
- **Media size:** Photos up to 8 MB, Videos up to 1 GB (reels up to 15 min)

## Troubleshooting

### "Object does not exist, cannot be loaded due to missing permissions"

**Cause:** Page and Instagram account are not linked, or token doesn't have the right permissions.

**Solution:**
1. Verify the Page → IG link: query `/{page_id}?fields=instagram_business_account`
2. If no `instagram_business_account` returned, re-do Step 5 (link IG to Page)
3. Generate a new token after linking (tokens don't pick up permissions retroactively)

### "/me/accounts returns 0 pages"

**Cause:** Token was generated before the page admin role was established, or the app doesn't have `pages_show_list` permission.

**Solution:**
1. Generate a **new** User Access Token in Graph API Explorer
2. Ensure `pages_show_list` permission is checked
3. The `/me/accounts` endpoint only returns pages where you're an admin

### "Error validating client secret"

**Cause:** App Secret was regenerated after you last used it.

**Solution:**
1. Go to: https://developers.facebook.com/apps/YOUR_APP_ID/settings/basic/
2. Click "Show" next to App Secret
3. Use the current secret in the token exchange request

### "It looks like this app isn't available"

**Cause:** Instagram Graph API product not added to the app.

**Solution:**
1. Go to your app dashboard
2. Click **Add Product** → find **Instagram Graph API** → **Set Up**
3. Add required permissions
4. Retry Graph API Explorer

### Page token expired unexpectedly

**Cause:** Page Access Tokens derived from long-lived tokens are permanent **unless**: the user changes their password, the user de-authorizes the app, or the app secret is reset.

**Solution:**
1. Repeat Steps 6–9 to generate a fresh permanent token
2. If you reset the App Secret, all existing tokens are invalidated

### Instagram account shows as "Personal" not "Business/Creator"

**Cause:** Instagram Graph API only works with Professional (Business or Creator) accounts.

**Solution:**
1. Instagram app → Settings → Account → Switch to Professional Account
2. Choose Creator or Business
3. Re-link to Facebook Page (Step 5)

## Security Notes

- **Never commit** `instagram_credentials.json` — it's in `.gitignore`
- **Never share** the App Secret or Page Access Token in chat, issues, or logs
- **Regenerate** the App Secret if it's ever exposed: https://developers.facebook.com/apps/YOUR_APP_ID/settings/basic/
- The permanent Page Token becomes invalid if the App Secret is reset — you'll need to generate a new one

## Files

| File | Purpose | Git-tracked |
|------|---------|-------------|
| `instagram_credentials.json` | App ID, IG user ID, page token | **No** (`.gitignore`) |
| `upload_instagram.py` | Upload script (photos + reels) | Yes |
| `INSTAGRAM_SETUP.md` | This setup guide | Yes |

