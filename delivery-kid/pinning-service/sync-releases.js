#!/usr/bin/env node
/**
 * Sync releases from PickiPedia to delivery-kid
 *
 * Queries PickiPedia for pages with IPFS CID property,
 * verifies the editor is in the 'release' group,
 * and pins any missing CIDs.
 *
 * Usage:
 *   node sync-releases.js          # Dry run
 *   node sync-releases.js --apply  # Actually pin
 */

const WIKI_API = process.env.WIKI_API || 'https://pickipedia.xyz/api.php';
const PINNING_API = process.env.PINNING_API || 'http://localhost:3001';
const REQUIRED_GROUP = process.env.REQUIRED_GROUP || 'release';

async function querySemanticWiki(query) {
    const url = new URL(WIKI_API);
    url.searchParams.set('action', 'ask');
    url.searchParams.set('query', query);
    url.searchParams.set('format', 'json');

    const response = await fetch(url);
    if (!response.ok) {
        throw new Error(`Wiki API error: ${response.status}`);
    }
    return response.json();
}

async function getUserGroups(username) {
    const url = new URL(WIKI_API);
    url.searchParams.set('action', 'query');
    url.searchParams.set('list', 'users');
    url.searchParams.set('ususers', username);
    url.searchParams.set('usprop', 'groups');
    url.searchParams.set('format', 'json');

    const response = await fetch(url);
    if (!response.ok) {
        throw new Error(`Wiki API error: ${response.status}`);
    }
    const data = await response.json();

    if (data.query?.users?.[0]?.groups) {
        return data.query.users[0].groups;
    }
    return [];
}

async function getPageLastEditor(title) {
    const url = new URL(WIKI_API);
    url.searchParams.set('action', 'query');
    url.searchParams.set('titles', title);
    url.searchParams.set('prop', 'revisions');
    url.searchParams.set('rvprop', 'user');
    url.searchParams.set('rvlimit', '1');
    url.searchParams.set('format', 'json');

    const response = await fetch(url);
    if (!response.ok) {
        throw new Error(`Wiki API error: ${response.status}`);
    }
    const data = await response.json();

    const pages = data.query?.pages;
    if (pages) {
        const page = Object.values(pages)[0];
        if (page.revisions?.[0]?.user) {
            return page.revisions[0].user;
        }
    }
    return null;
}

async function getCurrentPins() {
    try {
        const response = await fetch(`${PINNING_API}/api/pins`);
        if (!response.ok) {
            console.error('Could not fetch current pins:', response.status);
            return new Set();
        }
        const data = await response.json();
        return new Set(data.pins.map(p => p.cid));
    } catch (error) {
        console.error('Could not connect to pinning service:', error.message);
        return new Set();
    }
}

async function pinCid(cid, name) {
    const response = await fetch(`${PINNING_API}/api/pin`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ cid, name })
    });

    if (!response.ok) {
        const error = await response.text();
        throw new Error(`Pin failed: ${error}`);
    }
    return response.json();
}

async function main() {
    const dryRun = !process.argv.includes('--apply');

    console.log('='.repeat(60));
    console.log('DELIVERY-KID RELEASE SYNC');
    console.log(dryRun ? '(DRY RUN - use --apply to pin)' : '(APPLYING CHANGES)');
    console.log('='.repeat(60));
    console.log();

    // Query PickiPedia for all pages with IPFS CID property
    console.log('Querying PickiPedia for releases with IPFS CIDs...');
    const query = '[[IPFS CID::+]]|?IPFS CID|?Magnet URI|?Release artist|?Release date';

    let wikiData;
    try {
        wikiData = await querySemanticWiki(query);
    } catch (error) {
        console.error('Failed to query wiki:', error.message);
        process.exit(1);
    }

    const results = wikiData.query?.results || {};
    const releases = Object.entries(results);

    console.log(`Found ${releases.length} release(s) with IPFS CIDs`);
    console.log();

    // Get current pins
    const currentPins = await getCurrentPins();
    console.log(`Currently pinned: ${currentPins.size} CIDs`);
    console.log();

    // Process each release
    const toPin = [];
    const skipped = [];
    const alreadyPinned = [];

    for (const [title, data] of releases) {
        const printouts = data.printouts || {};
        const ipfsCid = printouts['IPFS CID']?.[0];
        const magnetUri = printouts['Magnet URI']?.[0];
        const artist = printouts['Release artist']?.[0]?.fulltext;

        if (!ipfsCid) {
            console.log(`  ${title}: No CID found, skipping`);
            continue;
        }

        // Check who last edited the page
        const lastEditor = await getPageLastEditor(title);
        if (!lastEditor) {
            console.log(`  ${title}: Could not determine editor, skipping`);
            skipped.push({ title, reason: 'unknown editor' });
            continue;
        }

        // Check if editor is in release group
        const groups = await getUserGroups(lastEditor);
        if (!groups.includes(REQUIRED_GROUP) && !groups.includes('sysop') && !groups.includes('bureaucrat')) {
            console.log(`  ${title}: Editor '${lastEditor}' not in '${REQUIRED_GROUP}' group, skipping`);
            skipped.push({ title, reason: `editor '${lastEditor}' not authorized`, cid: ipfsCid });
            continue;
        }

        // Check if already pinned
        if (currentPins.has(ipfsCid)) {
            console.log(`  ${title}: Already pinned (${ipfsCid.slice(0, 12)}...)`);
            alreadyPinned.push({ title, cid: ipfsCid });
            continue;
        }

        console.log(`  ${title}: Will pin ${ipfsCid.slice(0, 12)}... (editor: ${lastEditor})`);
        toPin.push({
            title,
            cid: ipfsCid,
            magnet: magnetUri,
            artist,
            editor: lastEditor
        });
    }

    console.log();
    console.log('-'.repeat(60));
    console.log(`Summary:`);
    console.log(`  Already pinned: ${alreadyPinned.length}`);
    console.log(`  Skipped (unauthorized): ${skipped.length}`);
    console.log(`  To pin: ${toPin.length}`);
    console.log('-'.repeat(60));

    if (toPin.length === 0) {
        console.log('Nothing to pin.');
        return;
    }

    if (dryRun) {
        console.log();
        console.log('DRY RUN - not pinning. Run with --apply to pin.');
        console.log();
        console.log('Would pin:');
        for (const release of toPin) {
            console.log(`  - ${release.title}: ${release.cid}`);
        }
        return;
    }

    // Actually pin
    console.log();
    console.log('Pinning...');

    for (const release of toPin) {
        try {
            console.log(`  Pinning ${release.title}...`);
            await pinCid(release.cid, release.title);
            console.log(`    ✓ Pinned ${release.cid.slice(0, 12)}...`);
        } catch (error) {
            console.error(`    ✗ Failed: ${error.message}`);
        }
    }

    console.log();
    console.log('Done.');
}

main().catch(error => {
    console.error('Fatal error:', error);
    process.exit(1);
});
