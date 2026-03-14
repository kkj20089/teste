export default {
  async fetch(request, env) {
    const url = new URL(request.url)
    if (url.pathname === "/update.json") {
      return serveUpdateJson(env)
    }
    if (url.pathname === "/latest") {
      return redirectToLatestApk(env)
    }
    return new Response("Not found", { status: 404 })
  },
}

async function githubLatestRelease(repo) {
  const response = await fetch(`https://api.github.com/repos/${repo}/releases/latest`, {
    headers: {
      Accept: "application/vnd.github+json",
      "User-Agent": "revanced-update-worker",
    },
  })
  if (!response.ok) {
    throw new Error("github_release_fetch_failed")
  }
  return response.json()
}

async function serveUpdateJson(env) {
  try {
    const release = await githubLatestRelease(env.GITHUB_REPO)
    const updateAsset = (release.assets || []).find((asset) => asset.name === "update.json")
    if (!updateAsset) {
      return new Response("update.json not found", { status: 404 })
    }
    const manifestResponse = await fetch(updateAsset.browser_download_url, {
      headers: { "User-Agent": "revanced-update-worker" },
    })
    if (!manifestResponse.ok) {
      return new Response("manifest_download_failed", { status: 502 })
    }
    return new Response(await manifestResponse.text(), {
      status: 200,
      headers: {
        "content-type": "application/json; charset=utf-8",
        "cache-control": "public, max-age=300",
      },
    })
  } catch (error) {
    return new Response(String(error), { status: 500 })
  }
}

async function redirectToLatestApk(env) {
  try {
    const release = await githubLatestRelease(env.GITHUB_REPO)
    const apkAsset = (release.assets || []).find((asset) => asset.name.endsWith(".apk"))
    if (!apkAsset) {
      return new Response("apk_not_found", { status: 404 })
    }
    return Response.redirect(apkAsset.browser_download_url, 302)
  } catch (error) {
    return new Response(String(error), { status: 500 })
  }
}
