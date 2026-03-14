export default {
  async fetch(request, env) {
    const url = new URL(request.url)
    if (url.pathname === "/update.json") {
      return serveUpdateJson(env)
    }
    if (url.pathname === "/latest") {
      return redirectToLatestApk(env)
    }
    if (url.pathname === "/") {
      return serveWebUI(env)
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
    return null
  }
  return response.json()
}

async function serveWebUI(env) {
  try {
    const myRelease = await githubLatestRelease(env.GITHUB_REPO)
    const revancedRelease = await githubLatestRelease("ReVanced/revanced-patches")
    
    let currentPatchedVersion = "Unknown"
    let currentDownloadUrl = "/latest"
    let lastBuildDate = "Unknown"
    
    if (myRelease) {
      lastBuildDate = new Date(myRelease.published_at).toLocaleString()
      const apkAsset = (myRelease.assets || []).find(a => a.name.endsWith(".apk"))
      if (apkAsset) currentDownloadUrl = apkAsset.browser_download_url
      currentPatchedVersion = myRelease.name || myRelease.tag_name
    }
    
    let latestRevancedPatches = "Unknown"
    if (revancedRelease) {
      latestRevancedPatches = revancedRelease.tag_name
    }

    const html = `
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>ReVanced YouTube Updater</title>
    <style>
        body { font-family: system-ui, -apple-system, sans-serif; max-width: 600px; margin: 0 auto; padding: 20px; background: #f4f4f5; color: #333; }
        .card { background: white; padding: 20px; border-radius: 12px; box-shadow: 0 4px 6px rgba(0,0,0,0.1); margin-bottom: 20px; }
        h1 { color: #d32f2f; }
        h2 { margin-top: 0; }
        .btn { display: inline-block; background: #d32f2f; color: white; padding: 12px 24px; text-decoration: none; border-radius: 8px; font-weight: bold; margin-top: 10px; }
        .btn:hover { background: #b71c1c; }
        .info { color: #666; font-size: 0.9em; }
    </style>
</head>
<body>
    <h1>ReVanced YouTube Updater</h1>
    
    <div class="card">
        <h2>📥 Latest Patched Build</h2>
        <p><strong>Version:</strong> ${currentPatchedVersion}</p>
        <p><strong>Built on:</strong> ${lastBuildDate}</p>
        <a href="${currentDownloadUrl}" class="btn">Download Patched APK</a>
    </div>

    <div class="card">
        <h2>🔄 Upstream Status</h2>
        <p><strong>Latest ReVanced Patches:</strong> ${latestRevancedPatches}</p>
        <p class="info">When ReVanced releases new patches, the GitHub Actions workflow will automatically run and build a new APK if the YouTube version changes.</p>
        <p class="info">Note: The GitHub Action requires a YouTube APK (nodpi) link to be provided manually if APKMirror blocks the automated download.</p>
    </div>
</body>
</html>`

    return new Response(html, {
      status: 200,
      headers: {
        "content-type": "text/html; charset=utf-8",
        "cache-control": "public, max-age=60",
      },
    })
  } catch (error) {
    return new Response(`Error rendering page: ${error}`, { status: 500 })
  }
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
