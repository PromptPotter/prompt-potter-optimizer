// Single source of truth for owner / hosting identity. Template forks
// override these values; everything else in the webapp reads from here.

export const instance = {
  owner: {
    name: "David Streuli",
    linkedin: "https://www.linkedin.com/in/david-streuli/",
  },
  marketing_url: "https://promptpotter.dev",
} as const;
