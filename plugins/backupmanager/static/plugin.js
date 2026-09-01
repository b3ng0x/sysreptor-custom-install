/**
 * BackupManager plugin frontend entry point.
 */
export default function (options) {
  options.pluginHelpers.addRoute({
    scope: 'main',
    route: {
      path: 'backups',
      component: () => options.pluginHelpers.iframeComponent({
        src: 'index.html',
      }),
    },
    menu: {
      title: 'Backups',
    },
  });
}
