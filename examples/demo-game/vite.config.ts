import { gamestudio } from '@gamestudio/devserver';
import { defineConfig } from 'vite';

export default defineConfig({
  plugins: [gamestudio()],
});
