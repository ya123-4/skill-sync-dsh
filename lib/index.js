import { createRequire } from 'node:module';
import { dirname, join } from 'node:path';

export const name = 'skill-sync-dsh';
export const PACKAGE_NAME = 'skill-sync-dsh';

/**
 * Resolve this package's bundled Skill root from the DSH profile anchor.
 * The bundle patch resolves the same directory directly; this export exists so
 * the resolution rule is documented in code and reusable by consumers.
 */
export function resolveSkillSyncSkillRoot(profileBaseUrl) {
  if (!profileBaseUrl) {
    throw new Error(`${PACKAGE_NAME}: missing DSH profile baseUrl for package resolution`);
  }
  let manifestPath;
  try {
    manifestPath = createRequire(profileBaseUrl).resolve(`${PACKAGE_NAME}/package.json`);
  } catch (error) {
    throw new Error(`${PACKAGE_NAME}: cannot resolve ${PACKAGE_NAME}/package.json from the DSH profile`, {
      cause: error,
    });
  }
  return join(dirname(manifestPath), 'skills');
}
