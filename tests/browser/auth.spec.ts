import { randomUUID } from "node:crypto";
import { expect, type Page, test } from "@playwright/test";

test.use({ trace: "off" });

async function signUp(page: Page, email: string, password: string) {
  await page.goto("/signup");
  await page.getByLabel("Email", { exact: true }).fill(email);
  await page.getByLabel("Password", { exact: true }).fill(password);
  await page.getByRole("button", { name: "Create account", exact: true }).click();
  await expect(page).toHaveURL(/\/dashboard(?:\?.*)?$/);
}

test("accounts can create, join, and lose access to a team", async ({ page, browser, baseURL }) => {
  test.skip(!process.env.BROWSER_BASE_URL, "Requires the real PostgreSQL/Redis Compose stack");
  test.setTimeout(90_000);

  const suffix = randomUUID();
  const ownerEmail = `owner-${suffix}@example.test`;
  const memberEmail = `member-${suffix}@example.test`;
  const teamName = `Browser workspace ${suffix.slice(0, 8)}`;
  const ownerPassword = randomUUID();
  const memberPassword = randomUUID();

  await signUp(page, ownerEmail, ownerPassword);
  const sessionCookie = (await page.context().cookies()).find(
    (cookie) => cookie.name === "aka_session",
  );
  expect(sessionCookie?.httpOnly).toBe(true);
  expect(sessionCookie?.sameSite).toBe("Lax");
  expect(await page.evaluate(() => document.cookie.includes("aka_session"))).toBe(false);

  await page.getByLabel("Team name", { exact: true }).fill(teamName);
  await page.getByRole("button", { name: "Create team", exact: true }).click();
  const teamLink = page.getByRole("link", { name: teamName, exact: true });
  await expect(teamLink).toBeVisible();
  await teamLink.click();

  await page.getByLabel("Invite email", { exact: true }).fill(memberEmail);
  await page.getByRole("combobox", { name: "Invite role", exact: true }).selectOption("member");
  await page.getByRole("button", { name: "Create invite", exact: true }).click();
  const invite = page.getByLabel("New invite token", { exact: true });
  await expect(invite).toBeVisible();
  const token = await invite.evaluate((element) =>
    element instanceof HTMLInputElement ? element.value : (element.textContent ?? "").trim(),
  );
  expect(token.length).toBeGreaterThan(24);

  const memberContext = await browser.newContext({ baseURL });
  try {
    const memberPage = await memberContext.newPage();
    await signUp(memberPage, memberEmail, memberPassword);
    await memberPage.getByLabel("Invite token", { exact: true }).fill(token);
    await memberPage.getByRole("button", { name: "Join team", exact: true }).click();
    const memberTeam = memberPage.getByRole("link", { name: teamName, exact: true });
    await expect(memberTeam).toBeVisible();
    await memberTeam.click();
    await expect(
      memberPage.getByRole("button", { name: "Create invite", exact: true }),
    ).toHaveCount(0);

    await page.reload();
    await expect(page.getByText(memberEmail, { exact: true })).toBeVisible();
    page.once("dialog", (dialog) => dialog.accept());
    await page.getByRole("button", { name: `Remove ${memberEmail}`, exact: true }).click();
    await expect(page.getByText(memberEmail, { exact: true })).toHaveCount(0);

    // The other browser still has the original session, but its team access is gone.
    await memberPage.reload();
    await expect(memberPage.getByRole("heading", { name: "Workspace overview" })).toBeVisible();
    await expect(memberTeam).toHaveCount(0);
  } finally {
    await memberContext.close();
  }

  await page.getByRole("button", { name: "Sign out", exact: true }).click();
  await expect(page).toHaveURL(/\/login(?:\?.*)?$/);
  await page.goto("/dashboard");
  await expect(page).toHaveURL(/\/login(?:\?.*)?$/);
  await page.getByLabel("Email", { exact: true }).fill(ownerEmail);
  await page.getByLabel("Password", { exact: true }).fill(ownerPassword);
  await page.getByRole("button", { name: "Sign in", exact: true }).click();
  await expect(teamLink).toBeVisible();
});
