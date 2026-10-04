// Workspace scope comes from the launch context, never from whichever pane has focus.
export function workspaceAgents(agents, snapshot, env = process.env, all = false) {
  if (all) return agents;
  const own = env.HERDR_PANE_ID;
  const workspace = env.HERDR_WORKSPACE_ID || agents.find((agent) => own && agent.pane_id === own)?.workspace_id ||
    snapshot?.layouts?.find((layout) => own && layout.panes?.some((pane) => pane.pane_id === own))?.workspace_id;
  return workspace ? agents.filter((agent) => agent.workspace_id === workspace) : [];
}
