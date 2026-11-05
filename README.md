# hildejk.xyz

Plain HTML. No build step, nothing to install. Whatever is in this repository is what the website shows.

## How to edit

1. Open the page's file on github.com (see the table below) and click the pencil icon.
2. Change the text. Every page has a `Page content starts here` comment; only edit below it.
3. Click **Commit changes**. The site updates about a minute later.

| Page          | File                        |
|---------------|-----------------------------|
| Home          | `index.html`                |
| Research      | `research/index.html`       |
| Publications  | `publications/index.html`   |
| Teaching      | `teaching/index.html`       |
| Mentoring     | `mentoring/index.html`      |
| CV            | `cv/index.html`             |
| Contact       | `contact/index.html`        |
| Colors, fonts | `style.css`                 |

## Publications update themselves (almost)

On the 1st of every month, GitHub compares the publications page with [OpenAlex](https://openalex.org) (a free publication database that finds you through your ORCID). If it finds a new paper, or a preprint that has since been published, it opens a **pull request** and GitHub emails you. Then:

1. Open the pull request and look at **Files changed**.
2. If it looks right, click **Merge pull request**. The site updates a minute later.
3. If not, fix the text in that pull request, or close it.

Nothing is published until you merge. If nothing changed, you hear nothing. To run the check right away: **Actions** tab → **Check publications** → **Run workflow**.

- **A paper that should never be listed** (an erratum, a duplicate): add its ID, shown in the pull request, to `.github/scripts/publications-ignore.txt`.
- **GitHub pauses monthly jobs after 60 days with no commits.** It emails you first. If that happens, go to **Actions** → **Check publications** → **Enable workflow**. Any commit, including merging one of these pull requests, also keeps it running.
- The script is `.github/scripts/update_publications.py`. It only adds papers and moves preprints; it never deletes anything else.

## Common tasks

**Add a paper by hand.** In `publications/index.html`, copy an existing `<li> … </li>` block, paste it at the top of the right section, and change the details. There is a ready-to-copy example in a comment at the top of the list.

**Add a student.** In `mentoring/index.html`, copy one `<li> … </li>` line and edit it.

**Replace the CV PDF.** Upload the new file as `cv/hjk_cv.pdf` (same name), using **Add file → Upload files** on github.com.

**Replace the photo.** Upload a new `img/HJK_photo.jpg`. Keep it small, around 600 px tall.

**Add a link to the menu.** The menu is copied in every page (8 HTML files, including `404.html`). Use github.com's search, or any editor's "find in files", to update all of them.

## HTML cheat sheet

| You want            | Write                                        |
|---------------------|----------------------------------------------|
| A paragraph         | `<p>Text</p>`                                |
| Bold / italic       | `<b>bold</b>`, `<i>italic</i>`               |
| A link              | `<a href="https://example.com">text</a>`     |
| A list item         | `<li>Item</li>` (inside `<ul> … </ul>`)      |
| A heading           | `<h2>Heading</h2>`                           |
| Grey small text     | `<span class="note">text</span>`             |

## Other files

- `CNAME`: tells GitHub Pages the domain is hildejk.xyz. Don't delete it.
- `.github/workflows/deploy.yml`: publishes the files on every commit to `main`. You never need to touch it.
- `.github/workflows/update-publications.yml`: the monthly publication check described above.
- `favicon.svg`: the browser-tab icon (a canard cycle with head on a cubic critical manifold).
