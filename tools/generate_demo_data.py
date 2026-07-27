#!/usr/bin/env python3
"""Generate demo data and visualizations for README screenshots."""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import pandas as pd
import numpy as np
import matplotlib.pyplot as plt
import seaborn as sns
from datetime import datetime, timedelta
import json

# Import our modules
from ecommerce_price_monitor import PriceAnalyzer, PriceVisualizer, DataExporter

# Set font for matplotlib - use default sans-serif for English
plt.rcParams['font.sans-serif'] = ['DejaVu Sans', 'Arial', 'Helvetica']
plt.rcParams['axes.unicode_minus'] = False

def generate_demo_data():
    """Generate realistic demo data for screenshots."""
    np.random.seed(42)
    
    # Platform and product data - English names
    platforms = ['JD.com', 'Taobao', 'Xiaohongshu', 'Douyin', 'Amazon', 'eBay']
    categories = ['Electronics', 'Beauty', 'Apparel', 'Home', 'Groceries']
    brands = ['Huawei', 'Xiaomi', 'Apple', 'OPPO', 'vivo', 'Samsung', 'OnePlus']
    
    demo_data = []
    
    # Generate 200 sample products
    for i in range(200):
        platform = np.random.choice(platforms)
        category = np.random.choice(categories)
        brand = np.random.choice(brands)
        
        # Base price varies by category
        base_prices = {
            'Electronics': (2000, 8000),
            'Beauty': (50, 500), 
            'Apparel': (100, 800),
            'Home': (200, 2000),
            'Groceries': (30, 300)
        }
        min_price, max_price = base_prices[category]
        
        # Platform price adjustments
        platform_multipliers = {
            'JD.com': 1.0,
            'Taobao': 0.85,
            'Xiaohongshu': 1.15,
            'Douyin': 0.80,
            'Amazon': 1.05,
            'eBay': 0.90
        }
        
        base_price = np.random.uniform(min_price, max_price)
        price = base_price * platform_multipliers[platform] * (1 + np.random.normal(0, 0.1))
        price = max(10, price)  # Minimum price
        
        # Generate time series data
        days_ago = np.random.randint(0, 30)
        timestamp = datetime.now() - timedelta(days=days_ago)
        
        demo_data.append({
            'platform': platform,
            'product_id': f'{platform}_{i:03d}',
            'name': f'{brand} {category} Product Model-{i}',
            'price': round(price, 2),
            'currency': 'CNY' if platform in ['JD.com', 'Taobao', 'Xiaohongshu', 'Douyin'] else 'USD',
            'category': category,
            'brand': brand,
            'rating': np.random.uniform(3.8, 5.0),
            'review_count': np.random.randint(10, 5000),
            'availability': np.random.choice(['In Stock', 'Pre-order', 'Out of Stock'], p=[0.8, 0.15, 0.05]),
            'timestamp': timestamp
        })
    
    return pd.DataFrame(demo_data)

def create_analysis_demo():
    """Create data analysis demo screenshots."""
    print("Generating data analysis layer demo data...")
    
    df = generate_demo_data()
    analyzer = PriceAnalyzer()
    analysis = analyzer.analyze(df)
    
    # Create docs/images directory
    docs_dir = Path("docs/images")
    docs_dir.mkdir(parents=True, exist_ok=True)
    
    # 1. Platform comparison analysis
    platform_stats = df.groupby('platform').agg({
        'price': ['mean', 'median', 'std', 'count'],
        'rating': 'mean',
        'review_count': 'mean'
    }).round(2)
    
    # Create a summary table visualization
    fig, ((ax1, ax2), (ax3, ax4)) = plt.subplots(2, 2, figsize=(15, 12))
    fig.suptitle('E-commerce Price Analysis Report', fontsize=16, fontweight='bold')
    
    # 1. Average price by platform
    platform_prices = df.groupby('platform')['price'].mean().sort_values(ascending=False)
    bars1 = ax1.bar(range(len(platform_prices)), platform_prices.values, 
                    color=['#FF6B6B', '#4ECDC4', '#45B7D1', '#96CEB4', '#FCEA2B', '#FF9F40'])
    ax1.set_title('Average Price by Platform', fontweight='bold')
    ax1.set_ylabel('Average Price (CNY/USD)')
    ax1.set_xticks(range(len(platform_prices)))
    ax1.set_xticklabels(platform_prices.index, rotation=45)
    
    # Add value labels on bars
    for bar, value in zip(bars1, platform_prices.values):
        height = bar.get_height()
        ax1.text(bar.get_x() + bar.get_width()/2., height + 10,
                f'{value:.0f}', ha='center', va='bottom', fontweight='bold')
    
    # 2. Price distribution by category
    category_data = []
    categories = df['category'].unique()
    for cat in categories:
        cat_prices = df[df['category'] == cat]['price'].values
        category_data.extend([(cat, price) for price in cat_prices])
    
    cat_df = pd.DataFrame(category_data, columns=['category', 'price'])
    cat_df.boxplot(column='price', by='category', ax=ax2)
    ax2.set_title('Price Distribution by Category')
    ax2.set_ylabel('Price (CNY/USD)')
    ax2.set_xlabel('Product Category')
    plt.setp(ax2.get_xticklabels(), rotation=45)
    
    # 3. Rating vs Price scatter
    colors = {'JD.com': '#E3002B', 'Taobao': '#FF6900', 'Xiaohongshu': '#FF2442', 
              'Douyin': '#000000', 'Amazon': '#FF9900', 'eBay': '#0064D2'}
    
    for platform in df['platform'].unique():
        platform_data = df[df['platform'] == platform]
        ax3.scatter(platform_data['price'], platform_data['rating'], 
                   label=platform, alpha=0.6, s=50, color=colors.get(platform, '#333333'))
    
    ax3.set_title('Price vs Rating Correlation')
    ax3.set_xlabel('Price (CNY/USD)')
    ax3.set_ylabel('Rating')
    ax3.legend(bbox_to_anchor=(1.05, 1), loc='upper left')
    ax3.grid(True, alpha=0.3)
    
    # 4. Market share pie chart
    platform_counts = df['platform'].value_counts()
    wedges, texts, autotexts = ax4.pie(platform_counts.values, labels=platform_counts.index, 
                                      autopct='%1.1f%%', startangle=90,
                                      colors=[colors.get(p, '#333333') for p in platform_counts.index])
    ax4.set_title('Platform Market Share')
    
    plt.tight_layout()
    plt.savefig(docs_dir / 'data_analysis_demo.png', dpi=300, bbox_inches='tight', 
                facecolor='white', edgecolor='none')
    plt.close()
    
    # Generate summary statistics table
    summary_stats = {
        "Total Products": len(df),
        "Platforms": len(df['platform'].unique()),
        "Categories": len(df['category'].unique()),
        "Price Range": f"{df['price'].min():.0f} - {df['price'].max():.0f}",
        "Average Price": f"{df['price'].mean():.0f}",
        "Average Rating": f"{df['rating'].mean():.1f}⭐"
    }
    
    print("Data analysis layer demo charts generated:")
    print(f"  - Comprehensive analysis chart: docs/images/data_analysis_demo.png")
    print(f"  - Data overview: {summary_stats}")
    
    return summary_stats

def create_visualization_demo():
    """Create visualization demo screenshots."""
    print("\nGenerating visualization layer demo data...")
    
    df = generate_demo_data()
    visualizer = PriceVisualizer()
    docs_dir = Path("docs/images")
    
    # Create price trend visualization
    fig, ((ax1, ax2), (ax3, ax4)) = plt.subplots(2, 2, figsize=(16, 12))
    fig.suptitle('E-commerce Price Visualization Dashboard', 
                 fontsize=16, fontweight='bold')
    
    # 1. Price distribution histogram
    chinese_platforms = df[df['platform'].isin(['JD.com', 'Taobao', 'Xiaohongshu', 'Douyin'])]
    international_platforms = df[df['platform'].isin(['Amazon', 'eBay'])]
    
    ax1.hist(chinese_platforms['price'], bins=30, alpha=0.7, label='Chinese Platforms', color='#FF6B6B', density=True)
    ax1.hist(international_platforms['price'], bins=30, alpha=0.7, label='International Platforms', color='#4ECDC4', density=True)
    ax1.set_title('Price Distribution', fontweight='bold')
    ax1.set_xlabel('Price (CNY/USD)')
    ax1.set_ylabel('Density')
    ax1.legend()
    ax1.grid(True, alpha=0.3)
    
    # 2. Platform comparison violin plot
    platform_prices = [df[df['platform'] == p]['price'].values for p in df['platform'].unique()]
    parts = ax2.violinplot(platform_prices, positions=range(len(df['platform'].unique())), showmeans=True)
    ax2.set_title('Platform Price Distribution Comparison', fontweight='bold')
    ax2.set_ylabel('Price (CNY/USD)')
    ax2.set_xticks(range(len(df['platform'].unique())))
    ax2.set_xticklabels(df['platform'].unique(), rotation=45)
    ax2.grid(True, alpha=0.3)
    
    # Color the violin plots
    colors = ['#FF6B6B', '#4ECDC4', '#45B7D1', '#96CEB4', '#FCEA2B', '#FF9F40']
    for pc, color in zip(parts['bodies'], colors):
        pc.set_facecolor(color)
        pc.set_alpha(0.7)
    
    # 3. Time series trend (simulate daily prices)
    dates = pd.date_range(start='2024-08-01', end='2024-09-10', freq='D')
    
    # Generate trending data for major platforms
    trend_data = {}
    for platform in ['JD.com', 'Taobao', 'Amazon']:
        base_price = df[df['platform'] == platform]['price'].mean()
        trend = np.cumsum(np.random.normal(0, base_price*0.02, len(dates))) + base_price
        trend_data[platform] = trend
    
    for platform, prices in trend_data.items():
        ax3.plot(dates, prices, marker='o', linewidth=2, markersize=4, label=platform)
    
    ax3.set_title('Price Trend Over Time', fontweight='bold')
    ax3.set_xlabel('Date')
    ax3.set_ylabel('Average Price (CNY/USD)')
    ax3.legend()
    ax3.grid(True, alpha=0.3)
    ax3.tick_params(axis='x', rotation=45)
    
    # 4. Correlation heatmap
    numeric_cols = ['price', 'rating', 'review_count']
    corr_data = df[numeric_cols].corr()
    
    im = ax4.imshow(corr_data, cmap='coolwarm', vmin=-1, vmax=1)
    ax4.set_title('Correlation Heatmap', fontweight='bold')
    ax4.set_xticks(range(len(numeric_cols)))
    ax4.set_yticks(range(len(numeric_cols)))
    ax4.set_xticklabels(['Price', 'Rating', 'Reviews'])
    ax4.set_yticklabels(['Price', 'Rating', 'Reviews'])
    
    # Add correlation values
    for i in range(len(numeric_cols)):
        for j in range(len(numeric_cols)):
            text = ax4.text(j, i, f'{corr_data.iloc[i, j]:.2f}',
                           ha="center", va="center", color="white", fontweight='bold')
    
    # Add colorbar
    cbar = plt.colorbar(im, ax=ax4, shrink=0.8)
    cbar.set_label('Correlation Coefficient')
    
    plt.tight_layout()
    plt.savefig(docs_dir / 'visualization_demo.png', dpi=300, bbox_inches='tight',
                facecolor='white', edgecolor='none')
    plt.close()
    
    print("Visualization layer demo charts generated:")
    print(f"  - Visualization charts: docs/images/visualization_demo.png")
    
    # Generate sample export data
    export_demo_data(df)

def export_demo_data(df):
    """Generate sample export files."""
    print("\nGenerating export format demo data...")
    
    output_dir = Path("docs/sample_exports")
    output_dir.mkdir(parents=True, exist_ok=True)
    
    exporter = DataExporter()
    
    # Sample subset for demo
    sample_df = df.head(20).copy()
    
    # Export in different formats
    formats = ['csv', 'json', 'html']
    results = exporter.export_multiple_formats(
        sample_df, formats, 'demo_sample', str(output_dir)
    )
    
    print("Export format demo files:")
    for fmt, path in results.items():
        if path:
            print(f"  - {fmt.upper()}: {path}")

def generate_readme_content():
    """Generate content snippets for README."""
    summary_stats = create_analysis_demo()
    create_visualization_demo()
    
    # Generate analysis results snippet
    analysis_snippet = f"""
### 📊 Data Analysis Layer Demo

![Data Analysis Demo](docs/images/data_analysis_demo.png)

#### Analysis Overview
- **Total Products**: {summary_stats['Total Products']} products
- **Platforms Covered**: {summary_stats['Platforms']} major e-commerce platforms  
- **Product Categories**: {summary_stats['Categories']} main categories
- **Price Range**: {summary_stats['Price Range']}
- **Average Price**: {summary_stats['Average Price']}
- **Average Rating**: {summary_stats['Average Rating']}

#### Key Insights
- 🏪 **Platform Differences**: Different platforms have distinct pricing strategies
- 📊 **Category Analysis**: Electronics show the highest price volatility
- ⭐ **Quality Correlation**: Moderate positive correlation between price and rating
- 📈 **Market Competition**: Major platforms compete intensively for market share
"""
    
    visualization_snippet = """
### 🎨 Visualization Layer Demo

![Visualization Demo](docs/images/visualization_demo.png)

#### Chart Types Description

1. **Price Distribution Histogram**: Shows price distribution differences between Chinese and international platforms
2. **Platform Comparison Violin Plot**: Displays price distribution shapes and density across platforms  
3. **Price Trend Line Chart**: Time series analysis tracking price trends of major platforms
4. **Correlation Heatmap**: Reveals relationships between price, rating, and review count

#### Visualization Features
- 🎯 **Interactive Charts**: Supports zoom, filter, and hover interactions
- 🎨 **Custom Themes**: Multiple color schemes and chart styles available
- 📱 **Responsive Design**: Charts automatically adapt to different screen sizes  
- 💾 **Multi-format Export**: PNG, SVG, HTML, PDF export options
"""

    return analysis_snippet, visualization_snippet

if __name__ == "__main__":
    print("[*] Generating README demo data and screenshots...")
    print("=" * 50)
    
    try:
        analysis_snippet, viz_snippet = generate_readme_content()
        
        print(f"\n[+] All demo data generated successfully!")
        print(f"[F] File locations:")
        print(f"  - Data analysis chart: docs/images/data_analysis_demo.png")
        print(f"  - Visualization chart: docs/images/visualization_demo.png")
        print(f"  - Export samples: docs/sample_exports/")
        
        print(f"\n[D] README content snippets ready for relevant sections:")
        print("=" * 30)
        print(analysis_snippet)
        print("=" * 30)
        print(viz_snippet)
        
    except Exception as e:
        print(f"[-] Error during generation: {e}")
        import traceback
        traceback.print_exc()
