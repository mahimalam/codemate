interface Window {
  desktopApp?: {
    openDirectory: () => Promise<{ canceled: boolean; path?: string }>;
  };
}

declare module '*.css';
declare module '*?worker' {
  const WorkerFactory: { new (): Worker };
  export default WorkerFactory;
}
