/** @type {import('next').NextConfig} */
const nextConfig = {
  // 隐藏 dev 左下角的问题指示气泡
  devIndicators: false,
  experimental: {
    serverActions: {
      bodySizeLimit: "10mb",
    },
  },
};

export default nextConfig;
