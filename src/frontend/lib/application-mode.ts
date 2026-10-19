export const APPLICATION_MODE = process.env.NEXT_PUBLIC_APP_MODE ?? 'api';

export function isMockMode() {
  return APPLICATION_MODE === 'mock';
}
