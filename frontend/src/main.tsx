import { createRoot } from 'react-dom/client';
import { App, ConfigProvider } from 'antd';
import zhCN from 'antd/locale/zh_CN';
import Workbench from './App';
import 'antd/dist/reset.css';
import './style.css';

createRoot(document.getElementById('root')!).render(<ConfigProvider locale={zhCN} theme={{ token: { colorPrimary: '#285f9c', borderRadius: 5, fontFamily: '"Microsoft YaHei", system-ui, sans-serif', fontSize: 13, controlHeight: 34 }, components: { Button: { defaultShadow: 'none', primaryShadow: 'none' }, Card: { boxShadow: 'none' } } }}><App><Workbench /></App></ConfigProvider>);
